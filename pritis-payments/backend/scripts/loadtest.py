"""Load test for about 1,000 students (design section 14, phase 10).

Runs against a THROWAWAY database: the ledger can't be deleted from, so the test data can
only be removed by dropping the whole database. The script refuses any database whose name
doesn't contain "load", any production environment, and any email backend but "console"
(so no real emails go to the fake students).

    # 1. a throwaway database, migrated
    createdb pritis_dues_load
    DATABASE_URL=postgresql+asyncpg://.../pritis_dues_load alembic upgrade head
    # 2. a server on that database (same machine, another port)
    DATABASE_URL=... uvicorn app.main:app --port 8101 --workers 2
    # 3. seed and run (same DATABASE_URL, from backend/)
    DATABASE_URL=... python scripts/loadtest.py seed --students 1000
    DATABASE_URL=... python scripts/loadtest.py run --base-url http://127.0.0.1:8101
    # 4. throw it away
    dropdb pritis_dues_load

What it measures:
  browse    every student opens the dashboard: /me, /dues, the due, its quote, /payments
  logins    a sample of real password logins (argon2 is the slowest thing we do; the
            login rate limit allows 30 per 5 minutes per address, so only a sample)
  webhooks  every student "pays": a pending payment each, then Paystack's signed
            charge.success webhooks all at once; waits until every one is credited
Then checks the ledger balances and the rep's wallet equals the sum of the dues.
Passes if nothing errors, browse p95 stays under 1.5 s, and all payments are credited
within 3 minutes.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import statistics
import sys
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.clock import utcnow  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.models import (  # noqa: E402
    Class, ClassDue, ClassMembership, DueRevision, Payment, RepWallet, University, User,
)
from app.services import app_settings, fees, ledger, sessions  # noqa: E402

DOMAIN = "loadtest.example.com"  # .invalid fails email validation at login
PASSWORD = "LoadTest#2026"
BROWSE_P95_LIMIT_MS = 1500
CREDIT_TIMEOUT_S = 180


def guard() -> None:
    s = get_settings()
    db_name = make_url(s.database_url).database or ""
    problems = []
    if "load" not in db_name:
        problems.append(f"the database is {db_name!r}; use a throwaway one with 'load' in its name")
    if s.environment == "production":
        problems.append("ENVIRONMENT is production")
    if s.email_backend != "console":
        problems.append("EMAIL_BACKEND must be console (no real emails to fake students)")
    if problems:
        sys.exit("Refusing to run: " + "; ".join(problems))


# --- seed ------------------------------------------------------------------------------------


async def seed(students: int) -> None:
    guard()
    async with SessionLocal() as db:
        if await db.scalar(select(func.count()).select_from(User).where(User.email.like(f"%@{DOMAIN}"))):
            sys.exit("Already seeded. Drop the database and start again for a clean run.")
        university = await db.scalar(select(University.id).limit(1))
        pw = hash_password(PASSWORD)  # one hash for everyone: seeding stays fast
        now = utcnow()

        def person(i: int | str) -> User:
            return User(email=f"student{i}@{DOMAIN}", password_hash=pw, full_name=f"Load Student {i}",
                        phone="+2348031234567", university_id=university, level="200", email_verified_at=now)

        rep = person("rep")
        rep.email = f"rep@{DOMAIN}"
        db.add(rep)
        await db.flush()
        klass = Class(name="LOADTEST 200 Level", school="Load University", department="Testing", level="200",
                      session="2026/2027", rep_user_id=rep.id, invite_code="LOADTEST")
        db.add(klass)
        await db.flush()
        deadline = now + timedelta(days=30)
        due = ClassDue(class_id=klass.id, title="Load test due", amount_kobo=500_000, deadline=deadline,
                       status="approved", is_live=True, share_slug=uuid.uuid4().hex[:12])
        db.add(due)
        await db.flush()
        revision = DueRevision(due_id=due.id, version=1, status="approved", reviewed_at=now,
                               platform_fee_percent=Decimal("3"), snapshot={
                                   "title": due.title, "description": "", "amount_kobo": due.amount_kobo,
                                   "deadline": deadline.isoformat(), "require_matric": False})
        db.add(revision)
        await db.flush()
        due.live_revision_id = revision.id
        for i in range(students):
            user = person(i)
            db.add(user)
            await db.flush()
            db.add(ClassMembership(user_id=user.id, class_id=klass.id))
        await db.commit()
    print(f"Seeded {students} students in one class with a live ₦5,000 due.")


# --- run --------------------------------------------------------------------------------------


class Timings:
    def __init__(self) -> None:
        self.ms: dict[str, list[float]] = {}
        self.errors: dict[str, list[str]] = {}

    def add(self, name: str, ms: float, error: str | None = None) -> None:
        self.ms.setdefault(name, []).append(ms)
        if error:
            self.errors.setdefault(name, []).append(error)

    def report(self) -> None:
        print(f"\n{'endpoint':<28}{'n':>6}{'errors':>8}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}")
        for name, values in self.ms.items():
            v = sorted(values)
            p95 = v[min(len(v) - 1, int(len(v) * 0.95))]
            print(f"{name:<28}{len(v):>6}{len(self.errors.get(name, [])):>8}"
                  f"{statistics.median(v):>9.0f}{p95:>9.0f}{v[-1]:>9.0f}")
        for name, errs in self.errors.items():
            print(f"  {name}: e.g. {errs[0]}")

    def p95(self, prefix: str) -> float:
        v = sorted(x for k, vals in self.ms.items() if k.startswith(prefix) for x in vals)
        return v[min(len(v) - 1, int(len(v) * 0.95))] if v else 0.0


async def timed(t: Timings, name: str, call, ok=(200,)) -> httpx.Response | None:
    start = time.perf_counter()
    try:
        resp = await call()
    except httpx.HTTPError as exc:
        t.add(name, (time.perf_counter() - start) * 1000, repr(exc))
        return None
    t.add(name, (time.perf_counter() - start) * 1000,
          None if resp.status_code in ok else f"HTTP {resp.status_code} {resp.text[:120]}")
    return resp


async def run(base_url: str, concurrency: int, logins: int) -> None:
    guard()
    s = get_settings()
    t = Timings()
    async with SessionLocal() as db:
        students = (await db.scalars(
            select(User).where(User.email.like(f"student%@{DOMAIN}")).order_by(User.created_at)
        )).all()
        due = await db.scalar(select(ClassDue).where(ClassDue.title == "Load test due"))
        rep_id = (await db.get(Class, due.class_id)).rep_user_id
        if not students or due is None:
            sys.exit("Nothing seeded. Run the seed step first.")
        # Signed-in sessions made directly: the login rate limit would stop 1,000 logins from
        # one address, and the logins step measures login speed on its own.
        tokens = {}
        for u in students:
            _, session = await sessions.start_session(db, "user", u.id, "loadtest", timedelta(hours=1))
            tokens[u.id] = create_access_token("user", u.id, session.id)
        await db.commit()
    print(f"{len(students)} students, concurrency {concurrency}, against {base_url}")

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(base_url=base_url, timeout=60, limits=limits) as client:
        # Browse.
        async def browse(user: User) -> None:
            async with sem:
                cookies = {"dpp_access": tokens[user.id]}
                for name, path in (("browse /me", "/api/user/me"), ("browse /dues", "/api/user/dues"),
                                   ("browse /dues/{id}", f"/api/user/dues/{due.id}"),
                                   ("browse quote", f"/api/user/dues/{due.id}/quote"),
                                   ("browse /payments", "/api/user/payments")):
                    await timed(t, name, lambda p=path: client.get(p, cookies=cookies))

        started = time.perf_counter()
        await asyncio.gather(*(browse(u) for u in students))
        browse_s = time.perf_counter() - started
        print(f"browse: {len(students) * 5} requests in {browse_s:.1f}s "
              f"({len(students) * 5 / browse_s:.0f} req/s)")

        # Logins (a sample, within the rate limit).
        async def login(user: User) -> None:
            async with httpx.AsyncClient(base_url=base_url, timeout=60) as c:
                csrf = (await c.get("/api/user/auth/csrf")).json()["csrf_token"]
                await timed(t, "login (argon2)", lambda: c.post(
                    "/api/user/auth/login", json={"email": user.email, "password": PASSWORD},
                    headers={"X-CSRF-Token": csrf}))
        await asyncio.gather(*(login(u) for u in students[:logins]))

        # Webhooks: everyone pays at once.
        async with SessionLocal() as db:
            percent = Decimal((await app_settings.get_fee_settings(db)).paystack_fee_percent)
            q = fees.student_total(due.amount_kobo, percent, Decimal("3"))
            payments = []
            for u in students:
                p = Payment(reference=f"DPP-LOAD{uuid.uuid4().hex[:16].upper()}", user_id=u.id, due_id=due.id,
                            revision_id=due.live_revision_id, base_kobo=q.due_kobo,
                            service_charge_kobo=q.service_charge_kobo, paystack_part_kobo=q.paystack_part_kobo,
                            platform_part_kobo=q.platform_part_kobo, total_kobo=q.total_kobo, status="pending")
                db.add(p)
                payments.append(p)
            await db.commit()

        async def webhook(i: int, p: Payment) -> None:
            body = json.dumps({"event": "charge.success", "data": {
                "id": 9_000_000 + i, "status": "success", "reference": p.reference, "amount": p.total_kobo,
                "currency": "NGN", "fees": 17_838, "channel": "card", "paid_at": utcnow().isoformat()}}).encode()
            sig = hmac.new(s.paystack_secret_key.encode(), body, hashlib.sha512).hexdigest()
            async with sem:
                await timed(t, "webhook charge.success", lambda: client.post(
                    "/api/webhooks/paystack", content=body,
                    headers={"x-paystack-signature": sig, "content-type": "application/json"}))

        if not s.paystack_secret_key:
            sys.exit("PAYSTACK_SECRET_KEY must be set (a test key) to sign the webhooks")
        started = time.perf_counter()
        await asyncio.gather(*(webhook(i, p) for i, p in enumerate(payments)))
        credited = 0
        while time.perf_counter() - started < CREDIT_TIMEOUT_S:
            async with SessionLocal() as db:
                credited = await db.scalar(select(func.count()).select_from(Payment).where(
                    Payment.reference.like("DPP-LOAD%"), Payment.status == "success"))
            if credited == len(payments):
                break
            await asyncio.sleep(1)
        credit_s = time.perf_counter() - started
        print(f"webhooks: {credited}/{len(payments)} payments credited in {credit_s:.1f}s")

    t.report()

    async with SessionLocal() as db:
        unbalanced = await ledger.unbalanced_transactions(db)
        wallet = await db.scalar(select(RepWallet).where(RepWallet.user_id == rep_id))
    expected_pending = credited * due.amount_kobo
    checks = {
        "no request errors": not any(t.errors.get(k) for k in t.ms if not k.startswith("login")),
        f"browse p95 under {BROWSE_P95_LIMIT_MS} ms ({t.p95('browse'):.0f} ms)": t.p95("browse") < BROWSE_P95_LIMIT_MS,
        "every payment credited": credited == len(payments),
        "ledger balances": not unbalanced,
        "rep wallet equals the dues paid": wallet is not None and wallet.pending_kobo == expected_pending,
    }
    print()
    for label, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    sys.exit(0 if all(checks.values()) else 1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python scripts/loadtest.py")
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("seed")
    s.add_argument("--students", type=int, default=1000)
    r = commands.add_parser("run")
    r.add_argument("--base-url", default="http://127.0.0.1:8101")
    r.add_argument("--concurrency", type=int, default=100)
    r.add_argument("--logins", type=int, default=20)
    args = parser.parse_args()
    if args.command == "seed":
        asyncio.run(seed(args.students))
    else:
        asyncio.run(run(args.base_url, args.concurrency, args.logins))


if __name__ == "__main__":
    main()
