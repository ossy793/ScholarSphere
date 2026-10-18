# Pritis Class Dues

Class reps collect dues online, students pay through Paystack, and every naira is tracked
in a ledger until it is paid out. The full design is in [docs/design.md](docs/design.md).
It is built in ten phases (design section 14). **Current status: phase 10 built (refunds, reminders, security pass, deployment files); next: deploy, staging, pilot.**

| Site | Audience | API prefix |
| --- | --- | --- |
| dpp.pritis.name.ng | Students and class reps | `/api/user`, `/api/webhooks` |
| admin.dpp.pritis.name.ng | Pritis staff | `/api/admin` |

## Layout

```
backend/    FastAPI app, Alembic migrations, tests
frontend/   user/, admin/, shared/  (plain HTML, CSS, JS — from phase 2)
deploy/     nginx configs, systemd units, release/backup scripts — see deploy/README.md
docs/       design document
```

## Local setup (Windows, PowerShell)

Requires Python 3.13 and PostgreSQL 16 or newer.

```powershell
cd pritis-payments\backend
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # then set your Postgres password and JWT_SECRET in .env
```

### Google sign-in (optional locally)

In Google Cloud Console > APIs & Services > Credentials, create an **OAuth client ID**
(type: Web application) with the authorised redirect URI
`http://localhost:8100/api/user/auth/google/callback` (add the production one,
`https://dpp.pritis.name.ng/api/user/auth/google/callback`, later). Put the client ID and
secret in `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. Without them the Google button
shows "not available yet".

Create the two databases (the test one is wiped on every test run):

```powershell
& "C:\Program Files\PostgreSQL\17\bin\createdb.exe" -U postgres pritis_dues
& "C:\Program Files\PostgreSQL\17\bin\createdb.exe" -U postgres pritis_dues_test
```

## Run

Easiest: two PowerShell windows, from any folder and any active environment. Each
script uses this project's `.venv` and refuses to start a second copy:

```powershell
& "C:\Users\hp\Desktop\OssyQuiz\pritis-payments\start-server.ps1"   # window 1: the website
& "C:\Users\hp\Desktop\OssyQuiz\pritis-payments\start-worker.ps1"   # window 2: the worker
```

Or by hand, from `pritis-payments\backend` with `.venv` active:

```powershell
alembic upgrade head                       # apply migrations
uvicorn app.main:app --reload --port 8100  # http://localhost:8100
```

Locally the API also serves the user site, so open http://localhost:8100/login.html.
In production Nginx serves the pages instead.

Emails (such as the 6-digit verification code) are queued in `email_outbox` and sent by a
separate process. Until an email provider is chosen, `EMAIL_BACKEND=console` prints them
to that process's log. In a second terminal:

```powershell
python -m app.worker                 # the background worker: leave it running
python -m app.worker --list          # its jobs and when each last ran
python -m app.worker --once --job reconciliation   # run a job now
```

(`python -m app.worker.outbox` still works and starts the same worker.) Unlike
`uvicorn --reload`, the worker does **not** pick up code changes: restart it (Ctrl+C,
then run it again) after updating the code.

| Job | Schedule | What it does |
| --- | --- | --- |
| email_outbox | every 10 s | Sends queued emails; retries with backoff, up to 5 attempts |
| webhook_retry | every minute | Processes stored Paystack events not yet processed |
| pending_payments | every 5 min | Verifies payments pending over 10 min; Abandoned after 24 h |
| settlement_check | hourly | Settled payments: rep's share Pending → Available |
| reconciliation | 02:00 Lagos | Ledger balance, wallets vs ledger, payments vs ledger, Paystack vs us; emails the accountant and super admin on a mismatch |

Job state is in `worker_jobs` (a restart neither skips nor repeats the nightly check), and
each job holds a PostgreSQL advisory lock while running, so a second worker can't double
up. If Paystack's settlement data isn't available, the accountant marks payments settled on
**Settlements** in the admin site after checking the bank statement.

Interactive API docs are at `/api/docs` everywhere except production.

### Sending real emails (Brevo)

1. In Brevo, authenticate the sending domain (Senders, Domains & Dedicated IPs > Domains)
   and add the DNS records it gives you in Cloudflare. If pritis.name.ng already has an
   SPF record (`v=spf1 …`), add Brevo's `include:` to it instead of creating a second one.
2. Create an API key (SMTP & API > API keys).
3. In `backend/.env`: `EMAIL_BACKEND=brevo`, `BREVO_API_KEY=…`, and `EMAIL_FROM` on that
   domain, e.g. `Pritis Class Dues <no-reply@pritis.name.ng>`.
4. Restart, then `python -m app.cli send-test-email --to you@example.com`. It sends at once
   and prints Brevo's reason if it fails (unverified sender, blocked IP, bad key).

If Brevo answers about an unauthorised IP, add the server's IP under Security >
Authorised IPs, or turn that check off.

## Admin site and staff accounts

Locally the admin site is at **http://admin.localhost:8100** (browsers send
`*.localhost` to this machine), so it keeps its own cookies like
admin.dpp.pritis.name.ng will. There is no public signup. Create the first super admin:

```powershell
python -m app.cli create-super-admin --email you@pritis.name.ng --name "Your Name"
python -m app.cli resend-invite --email you@pritis.name.ng   # if that link expired
python -m app.cli reset-authenticator --email you@pritis.name.ng   # lost phone, see below
```

**Lost or new phone:** the super admin clicks **Reset authenticator** next to the person
on Staff accounts. Their password and authenticator stop working, they are signed out
everywhere, and they get a new set-up link (same account, audit history kept). A super
admin can't reset themselves on the website, since a stolen password would then be
enough to take the account over; if the only super admin loses their phone, run
`reset-authenticator` on the server.

It prints an invitation link (also queued as an email). On that page the person sets a
password and scans a QR code with an authenticator app (Google Authenticator, Microsoft
Authenticator…). The super admin then invites the accountant and support staff from
**Staff accounts**, which shows the same kind of link.

- Staff log in with password, then a 6-digit authenticator code. Each code works once,
  5 wrong passwords lock the account for 15 minutes, and code guesses are rate limited.
  Sessions end 8 hours after login however active the person is.
- Authenticator secrets are encrypted with `DATA_ENCRYPTION_KEY`. Never change that key
  once there is data, or the secrets (and later rep bank details) can't be read.
- `require_roles(...)` guards every admin route; the browser only hides links.
- Every staff action writes an `audit_logs` row in the same transaction
  (`app/services/audit.py`). The super admin sees it on **Audit log**.
- **Settings** (super admin): Paystack and platform percentages, the payout fee, the
  maximum due amount (default ₦200,000) and Paystack transfer fee bands. Unsaved keys use
  the defaults in `app/services/app_settings.py`.

## Class reps

- A verified student applies at `/rep-apply.html` (or by choosing "I'm a class rep" at
  login): their details, the class (university, department, level, session, class name),
  a proof file (school ID or student email screenshot: JPG/PNG/WebP/PDF, max 5 MB, type
  checked from the file itself), and the payout bank account. The account name is looked
  up with Paystack before submitting and again on the server.
- The application registers the class. Account numbers are encrypted; screens show the
  last four digits. Proof files are private (`STORAGE_BACKEND=local` keeps them in
  `backend/var/uploads`; `r2` uses a Cloudflare R2 bucket) and are only served to
  reviewers through `/api/admin/rep-applications/{id}/proof`.
- Support, the accountant or the super admin approves or rejects (with a reason) on
  **Rep applications**. (The design document names only support and the super admin; the
  owner added the accountant.)
  Approving creates the Paystack transfer recipient; if Paystack refuses, the application
  stays pending. Emails go out on submit, approval and rejection.
- Rep routes depend on `get_current_rep`, which scopes them to the rep's own class; routes
  taking a class id use `require_own_class` (someone else's class answers 404).
- Paystack needs `PAYSTACK_SECRET_KEY` (test key, `sk_test_…`). Tests fake every Paystack call.

## Dues

The lifecycle (`app/services/dues.py`) follows design section 4:
draft → pending → approved → live, with rejected → draft, and live → edit pending → live.

- Reps create dues on `/rep-due.html` (save as draft or submit). Super admin or accountant
  approve on **Due approvals**, optionally changing the platform fee (default from
  Settings). Above the maximum due amount only the super admin can approve.
- An approved due's edits are `due_revisions` rows; `class_dues` keeps the live terms and
  `live_revision_id` moves only on approval. One change can wait at a time.
- The rep switches an approved due live or pauses it. Students see only live dues.
- After the deadline a due is closed; `ensure_payable()` refuses payment (phase 6 calls it).
  Extending the deadline is an edit that needs approval.
- Each due has a share link `/due.html?s=CODE`; opening it while logged in joins the class.
- Emails: approvers on submit (super admin and accountant), the rep on each decision.

## Payments and the ledger

- **Fees** (`app/services/fees.py`): service charge = Paystack part (settings) + platform
  part (per due), each a percentage of the due in whole kobo, rounded half up. **Students
  see only the total** (owner's decision; the design document showed a breakdown): the
  quote, due list, payment pages, receipt PDF and receipt email carry no due / service
  charge split, and the student API doesn't send it. The split is kept on each payment.
- **Paying** (`app/services/payments.py`): `POST /api/user/payments` recomputes the quote,
  inserts a Pending payment with a unique reference and calls Paystack Initialize; pressing
  Pay again within 30 minutes reuses that checkout. Closed or paused dues, a missing
  required matric number, an unverified email and paying twice are all refused.
- **Confirming**: only Paystack's signed `charge.success` webhook
  (`POST /api/webhooks/paystack`, HMAC-SHA512) or the server's own Verify Transaction call
  (made when `/payment-callback.html` polls) marks a payment paid, and only for the exact
  amount and currency. Events are stored once per key and processed after the response;
  repeats change nothing. References that aren't ours are ignored.
- **Ledger** (`app/services/ledger.py`): balanced, append-only entries (a database trigger
  refuses UPDATE/DELETE). A payment posts: debit Paystack clearing D+S−F, credit the rep's
  Pending D, credit platform revenue S−F. The rep's cached wallet is updated in the same
  transaction.
- **Receipts** (`app/services/receipts.py`): PDF built on demand (never stored), attached
  to the confirmation email and downloadable from the dashboard and payment pages.

### Testing payments locally

Put your Paystack test secret key in `PAYSTACK_SECRET_KEY`. Pay with Paystack's test card
(shown on the test checkout). The callback page confirms the payment through Verify, so no
webhook is needed locally. To test the webhook too, expose the server with a tunnel
(e.g. `cloudflared tunnel --url http://localhost:8100`) and set Paystack's **test webhook
URL** to `https://<tunnel>/api/webhooks/paystack`.

## Payouts

- **Rep side** (`/rep.html`, `/rep-due-payments.html`): per due, students / paid /
  collected / outstanding and who has or hasn't paid (refreshes every 30 s; paid list as
  PDF or CSV). Wallet: Pending, Available, On hold, payout history. A payout request shows
  the amount, one service charge line (payout fee % + Paystack transfer fee from the bands
  in Settings) and what the rep receives; one open request at a time, up to Available.
- **Admin side** (**Payouts**, accountant and super admin): the approval screen shows the
  rep's balances and history, the net amount, the account, and the Paystack balance;
  approval is blocked if the balance can't cover the transfer and its fee. Approving marks
  the payout Processing (committed first, so it can't be approved twice) and starts a
  Paystack transfer with the payout's unique reference.
- **Outcome**: `transfer.success` → Paid; `transfer.failed` / `transfer.reversed` or a
  refused transfer → the hold returns to Available. An unclear answer leaves it Processing
  and the `transfer_status_check` job resolves it by reference (never by resending).
- **Ledger**: request = debit rep available / credit payout hold; paid = debit payout hold /
  credit Paystack clearing N+T and payout fee revenue X; release = the reverse of request;
  a different actual transfer fee is posted between payout fee revenue and clearing.
- **Changing the payout account** (owner's decision): the rep submits a new account
  (looked up with Paystack); payouts pause until the accountant or super admin approves it
  on **Payouts → Bank account changes**, which creates the new Paystack transfer recipient.
- For transfers to work, Paystack's **OTP for API transfers must be off** (Settings →
  Preferences); otherwise payouts stay Processing with a note to finalise them on Paystack.

## Reports and support search

- **Overview** (admin home, every staff role): waiting approvals and today's figures.
- **Reports** (accountant and super admin): for a date range (default the last 30 days,
  at most 400): money in, Paystack fees, platform revenue, owed to reps, paid out, and
  charts (revenue by month, owed vs paid out, payments per day, channel mix). Every chart
  has a "Show as table" view. Export the payments as CSV, or the period statement as PDF.
- **Ledger** (accountant and super admin): account balances, entries, and reconciliation runs.
  Every figure comes from the ledger, so clearing = owed to reps + refunds being sent
  + revenue − Paystack fees Pritis absorbed on refunds.
- **Users / Payments** (support, accountant, super admin): read-only search by name,
  email, phone (any of `0803…`, `803…`, `234803…`), matric number, reference or receipt number.
- Days and months are Lagos time.

## Refunds

- **Who:** support, the accountant or the super admin starts one from **Payments**
  ("Start a refund…", with a reason); the accountant or super admin approves or rejects it
  on **Refunds**. Student and rep are emailed at each step; staff when one is requested or fails.
- **Whose mistake** (recorded at approval) decides the amount: Pritis's or the rep's → the
  whole payment back, Pritis absorbs Paystack's fee (`refund_fee_expense`); the student's →
  the payment less Paystack's fee.
- **Approval** checks the Paystack balance, reserves the rep's share (`refund_hold`: from
  Pending if the payment hasn't settled, else from Available, which may go below zero and is
  covered by later settlements before any payout), marks it Processing, then calls Paystack's
  Refund API. One open refund per payment; a payment with a refund being sent doesn't settle.
- **Outcome:** `refund.processed` → payment Refunded (the student can pay again; the rep's
  paid list drops it), ledger reversed. `refund.failed` or a refusal → reservation released.
  An unclear answer stays Processing; the 10-minute check asks Paystack (and gives up after an
  hour if Paystack never got it), so a refund is never sent twice.

## Payment reminders

The worker emails unpaid students of a live due at 08:00 Lagos time, 3 days before the
deadline and on the deadline day, once each (recorded in `payment_reminders`). The design's
weekly "overdue" reminder is left out: a due can't be paid after its deadline.

## Security

- Every response carries a Content Security Policy (no inline code anywhere, so no
  `unsafe-inline`), `nosniff`, `X-Frame-Options: DENY`, a referrer policy, and HSTS on HTTPS.
  API answers are `Cache-Control: no-store`. Nginx adds the same headers to the pages.
- `tests/test_security.py` checks **every** route: each needs the right session unless it's on
  a short reviewed public list; user and staff sessions never cross; reps can't reach another
  class's dues or members; students can't reach other students' payments or classes they
  haven't joined.
- Per-address rate limits are generous (a class often shares one public address); password
  guessing is stopped per account by the temporary lockout.

## Load test

`backend/scripts/loadtest.py` seeds 1,000 students and measures browsing, logins and 1,000
simultaneous payment webhooks against a throwaway database (it refuses any other). Run it on
the server before launch; see deploy/README.md step 12.

## How sign-in works

- Signup logs the user in at once and emails a 6-digit code. The code lasts 10 minutes,
  allows 5 wrong tries, and is stored only as a keyed hash. "Send a new code" cancels the
  old one and is limited to 3 per 10 minutes. An unverified user can browse but not pay
  or apply as a rep (`get_verified_user`).
- New passwords (signup, reset) need 8+ characters with an uppercase letter, a lowercase
  letter, a number and a symbol. The server enforces it (`check_password_strength`);
  `frontend/shared/password-rules.js` shows the same rules as a live checklist. Login does
  not apply the rules, so older passwords keep working.
- Forgot password: the login page links to `/forgot-password.html`. It emails a 6-digit
  reset code (same rules as above) and always answers the same way, so it can't be used
  to discover which emails have accounts. A reset signs out every other device, clears
  any lockout, and lets Google-only users set a password.
- Signup also asks for a Nigerian mobile number (stored as +234…), the university (a
  searchable list of 249 Nigerian universities in the `universities` table, seeded from
  `alembic/data/nigerian_universities.json`) and level (100–500). Google sign-ups fill
  these in on `/complete-profile.html` before they can continue; paying and applying as a
  rep also require them (`get_verified_user`). To add a missing university, insert a row
  into `universities` (an admin screen can come with the staff site).
- Google sign-in skips the code (Google has verified the email) and links to an existing
  password account with the same email.
- Sessions: a 15-minute access JWT and a rotating 7-day refresh token, both HttpOnly
  cookies. Refresh tokens are stored hashed in `auth_sessions`; replaying an old one ends
  every session of that account.
- CSRF: every POST/PUT/PATCH/DELETE under `/api` must send the `csrf_token` cookie value
  in an `X-CSRF-Token` header (`frontend/shared/api.js` does this). Webhooks are exempt.
- Five wrong passwords lock the account for 15 minutes. Auth routes are also rate limited
  per IP (limits in `app/services/rate_limit.py`, counted in PostgreSQL).

## Test

```powershell
pytest
```

The tests rebuild `TEST_DATABASE_URL` from the migrations and empty every table before
each test. Paystack and Google are mocked. They fail if a model changes without a
matching migration.

## Adding a schema change

Every schema change needs an Alembic migration:

```powershell
alembic revision --autogenerate -m "short description"
```

Review the generated file before committing.

## Conventions

- Money is always integer kobo; never floats.
- Every error response has one shape:
  `{"error": {"code": "...", "message": "...", "details": [...]}}`.
  Raise `app.core.errors.AppError` for expected errors.
- Secrets live only in environment variables (`backend/.env` locally,
  `/etc/pritis/.env` on the server).

## Storage

- **PostgreSQL** (on the VPS) is the only database: users, dues, payments, ledger, queues.
- **Cloudflare R2** stores files: rep proof uploads (phase 4) and the nightly
  encrypted `pg_dump` backups copied off the server (phase 10).

## Keeping pritis.name.ng untouched

This app shares a VPS with the existing pritis.name.ng site, so it is deployed
alongside it, never into it. The step-by-step runbook is [deploy/README.md](deploy/README.md):

- Its own Nginx files (`dpp.pritis.name.ng.conf`, `admin.dpp.pritis.name.ng.conf`);
  the existing pritis.name.ng server block is not edited.
- Its own certificates, requested with `certbot -d dpp.pritis.name.ng -d admin.dpp.pritis.name.ng`.
- Its own code folder (`/srv/pritis-dues`), Linux user (`dpp`), systemd services
  (`dpp-api`, `dpp-worker`, backup timers), local port (8100) and PostgreSQL database and role.
- DNS: only new A records for `dpp` and `admin.dpp` are added.
