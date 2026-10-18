# Deploying Pritis Class Dues

This puts the platform on **dpp.pritis.name.ng** (students and reps) and
**admin.dpp.pritis.name.ng** (staff), on the same VPS as pritis.name.ng, **without
changing pritis.name.ng**.

Everything here is new and separately named:

- user `dpp`
- folder `/srv/pritis-dues`
- secrets `/etc/pritis-dues`
- database `pritis_dues`
- services `dpp-*`
- Nginx files `dpp*`
- API port `8100`

No step edits an existing file. The only shared action is `systemctl reload nginx`, which
reloads every site without dropping a connection, and only after `nginx -t` says the full
configuration is valid.

Commands are for Ubuntu 22.04/24.04, run as a sudo user. Allow about 1–2 hours.

| File | Goes to |
|---|---|
| `nginx/dpp.pritis.name.ng.conf` | `/etc/nginx/sites-available/` |
| `nginx/admin.dpp.pritis.name.ng.conf` | `/etc/nginx/sites-available/` |
| `nginx/dpp-ratelimit.conf` | `/etc/nginx/conf.d/` |
| `nginx/dpp-headers.conf`, `dpp-proxy.conf`, `dpp-admin-allow.conf` | `/etc/nginx/snippets/` |
| `systemd/*` | `/etc/systemd/system/` |
| `env.production.example` | `/etc/pritis-dues/env` (filled in, mode 600) |
| `scripts/release.sh` | run for every release |
| `scripts/backup.sh`, `scripts/restore-test.sh` | run by the timers |

---

## 0. Look before touching anything

```bash
sudo ss -ltnp | grep -E ':8100\b' || echo "8100 is free"   # must be free
sudo nginx -t                                               # must already pass
ls /etc/nginx/sites-enabled/                                # note what's there
psql --version; sudo -u postgres psql -c 'select version()'  # PostgreSQL client and server
sudo ufw status verbose                                     # note the firewall rules
```

If 8100 is taken, pick another port. Replace `8100` in `systemd/dpp-api.service`,
`nginx/dpp-proxy.conf` and `scripts/release.sh`.

## 1. DNS (Cloudflare)

Add two **A** records pointing at the VPS's IP: `dpp` and `admin.dpp`. Set both to
**DNS only (grey cloud)**.

If they're proxied (orange cloud), every visitor appears to come from Cloudflare's address. That
breaks the per-address rate limits and the admin allow-list. Proxied mode would need extra
`real_ip` configuration.

Wait until `dig +short dpp.pritis.name.ng` shows the VPS address.

## 2. Packages

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa      # Python 3.13 (the project's version)
sudo apt update
sudo apt install -y python3.13 python3.13-venv git gnupg curl \
                    postgresql-client certbot python3-certbot-nginx
```

`pg_dump` must be at least the PostgreSQL server's major version. If the server is newer than the
client, install `postgresql-client-NN` from the PostgreSQL apt repository.

If PostgreSQL isn't installed yet (pritis.name.ng may use something else), run
`sudo apt install -y postgresql`. It listens on localhost only by default; keep it that way.

## 3. User, folders, code

```bash
sudo useradd --system --home-dir /srv/pritis-dues --shell /usr/sbin/nologin dpp
sudo mkdir -p /srv/pritis-dues/var/backups /srv/pritis-dues/var/uploads /etc/pritis-dues
sudo chown -R dpp:dpp /srv/pritis-dues
sudo chown root:dpp /etc/pritis-dues && sudo chmod 750 /etc/pritis-dues
```

The code is in the GitHub repository `ossy793/ScholarSphere` (the OssyQuiz folder). If that repository is private, add a read-only
**deploy key**: GitHub → repository → Settings → Deploy keys.

```bash
sudo -u dpp -H ssh-keygen -t ed25519 -N '' -f /srv/pritis-dues/.ssh/id_ed25519
sudo cat /srv/pritis-dues/.ssh/id_ed25519.pub     # paste into GitHub as a deploy key
sudo -u dpp -H git clone git@github.com:ossy793/ScholarSphere.git /srv/pritis-dues/repo
sudo -u dpp -H python3.13 -m venv /srv/pritis-dues/venv
sudo -u dpp -H /srv/pritis-dues/venv/bin/pip install -r /srv/pritis-dues/repo/pritis-payments/backend/requirements.txt
sudo chmod +x /srv/pritis-dues/repo/pritis-payments/deploy/scripts/*.sh
```

## 4. Database

```bash
DB_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))"); echo "$DB_PASS"   # keep it
sudo -u postgres psql <<SQL
CREATE ROLE pritis_dues LOGIN CREATEDB PASSWORD '$DB_PASS';
CREATE DATABASE pritis_dues OWNER pritis_dues;
SQL
```

`CREATEDB` lets the monthly restore test create and drop its scratch database.

## 5. Secrets

```bash
sudo install -o dpp -g dpp -m 600 /srv/pritis-dues/repo/pritis-payments/deploy/env.production.example /etc/pritis-dues/env
sudo nano /etc/pritis-dues/env       # fill in every value (see the comments in the file)
```

Generate **new** `JWT_SECRET` and `DATA_ENCRYPTION_KEY` values; never reuse your laptop's. Put
`DATA_ENCRYPTION_KEY` in your password manager.

For the first runs use the Paystack **test** key: that's the staging stage (step 14).

The backup passphrase encrypts every backup. **Keep a copy in your password manager**: without
it the backups can't be opened.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))" | sudo tee /etc/pritis-dues/backup-passphrase
sudo chown dpp:dpp /etc/pritis-dues/backup-passphrase && sudo chmod 600 /etc/pritis-dues/backup-passphrase
```

In Cloudflare R2, create two buckets, `pritis-dues-files` and `pritis-dues-backups`. Then create one
API token with **Object Read & Write** on those two buckets only.

On the backups bucket, add a lifecycle rule that deletes objects under `backups/` after, say,
90 days.

## 6. Tables and the first super admin

```bash
cd /srv/pritis-dues/repo/pritis-payments/backend
sudo -u dpp -H bash -c 'set -a; . /etc/pritis-dues/env; set +a; /srv/pritis-dues/venv/bin/alembic upgrade head'
sudo -u dpp -H bash -c 'set -a; . /etc/pritis-dues/env; set +a; /srv/pritis-dues/venv/bin/python -m app.cli create-super-admin --email you@pritis.name.ng --name "Your Name"'
```

The last command prints a link. Open it once the site is up (step 9) to set the password and
authenticator.

## 7. Certificate

One certificate covers both names. `certonly` doesn't edit any Nginx file.

```bash
sudo certbot certonly --nginx -d dpp.pritis.name.ng -d admin.dpp.pritis.name.ng
```

Renewal is automatic: certbot's existing timer covers it.

## 8. Nginx

```bash
cd /srv/pritis-dues/repo/pritis-payments/deploy/nginx
sudo cp dpp-ratelimit.conf /etc/nginx/conf.d/
sudo cp dpp-headers.conf dpp-proxy.conf dpp-admin-allow.conf /etc/nginx/snippets/
sudo cp dpp.pritis.name.ng.conf admin.dpp.pritis.name.ng.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/dpp.pritis.name.ng.conf /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/admin.dpp.pritis.name.ng.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

If `nginx -t` fails, nothing has changed for pritis.name.ng. Fix the problem, or remove the two
links, before reloading.

Nginx needs to read the pages: `sudo chmod o+x /srv/pritis-dues` (enter only, no listing).

## 9. Services

```bash
sudo cp /srv/pritis-dues/repo/pritis-payments/deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dpp-api.service dpp-worker.service dpp-backup.timer dpp-restore-test.timer
curl -s http://127.0.0.1:8100/api/health                    # {"status":"ok","database":"ok"}
curl -sI https://dpp.pritis.name.ng/ | grep -i -E 'strict-transport|content-security'
curl -sI https://pritis.name.ng/ | head -1                  # the main site still answers as before
```

Now open the super admin link from step 6.

To read logs:

```bash
journalctl -u dpp-api -f
journalctl -u dpp-worker -f
```

## 10. Firewall

Only change the firewall if `ufw status` (step 0) showed it inactive. Otherwise the needed rules
are probably there already.

```bash
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
```

PostgreSQL and port 8100 stay on localhost and need no rule. SSH should be key-only:
`PasswordAuthentication no` in `/etc/ssh/sshd_config`.

## 11. Backups: prove they restore

```bash
sudo systemctl start dpp-backup.service && journalctl -u dpp-backup -n 5
sudo systemctl start dpp-restore-test.service && journalctl -u dpp-restore-test -n 5   # ends "RESTORE TEST PASSED"
sudo -u dpp -H bash -c 'set -a; . /etc/pritis-dues/env; set +a; cd /srv/pritis-dues/repo/pritis-payments/backend; /srv/pritis-dues/venv/bin/python -m app.backups list'
```

From then on, a backup runs nightly at 02:30 Lagos time and a restore test on the 1st of each
month. The release script also makes a backup before every migration.

## 12. Load test (on the server, before real students)

This uses a throwaway database and a second API on port 8101, so the real data is untouched.
Run it in a shell as `dpp`:

```bash
sudo -u dpp -H bash
set -a; . /etc/pritis-dues/env; set +a
export EMAIL_BACKEND=console
MAIN_DB="${DATABASE_URL/+asyncpg/}"                       # the real database, only to create/drop the scratch one
export DATABASE_URL=${DATABASE_URL%/*}/pritis_dues_load
createdb --maintenance-db="$MAIN_DB" pritis_dues_load
cd /srv/pritis-dues/repo/pritis-payments/backend
/srv/pritis-dues/venv/bin/alembic upgrade head
/srv/pritis-dues/venv/bin/uvicorn app.main:app --port 8101 --workers 3 --log-level warning &
/srv/pritis-dues/venv/bin/python scripts/loadtest.py seed --students 1000
/srv/pritis-dues/venv/bin/python scripts/loadtest.py run --base-url http://127.0.0.1:8101 --concurrency 100
kill %1; dropdb --maintenance-db="$MAIN_DB" pritis_dues_load; exit
```

It passes when:

- no request errors
- the slowest 5% of page requests take under 1.5 s
- all 1,000 payments are credited
- the ledger balances

On the Windows development laptop, the money checks all passed (1,000 webhooks credited in 27 s,
ledger balanced). Page speed did not pass, because a PostgreSQL round trip there takes about
4 ms, many times slower than on Linux. So this server run is the one that counts. If it's slow,
first try `--workers 4` in `dpp-api.service`, and check `top` for memory.

## 13. Paystack, Brevo, Google

**Paystack** (dashboard → Settings → API Keys & Webhooks):

- Webhook URL: `https://dpp.pritis.name.ng/api/webhooks/paystack`
- If **pritis.name.ng already uses this account's webhook**, there's only one webhook URL per
  account. Use a separate Paystack account (or a subaccount with its own keys) for Class Dues;
  don't move the existing one.
- Settings → Preferences: turn **off OTP for transfers**, or payouts stay "Being sent".
- If Paystack asks for IP whitelisting for transfers, add the VPS's IP.
- Confirm with Paystack:
  - the transfer fee bands (Admin → Settings)
  - that the Refund API is enabled
  - how refunds work for bank transfer and USSD payments
  - that Paystack keeps its fee on refunds (the app assumes it does)

**Brevo:**

- Authenticate pritis.name.ng (Senders, Domains & Dedicated IPs → Domains). This adds DNS records
  in Cloudflare: SPF, DKIM and DMARC.
- Set `EMAIL_FROM="Pritis Class Dues <no-reply@pritis.name.ng>"`.
- Turn IP blocking back on with only the VPS's IP allowed.
- Test with `python -m app.cli send-test-email --to you@example.com`.

**Google sign-in:** add the authorised redirect URI
`https://dpp.pritis.name.ng/api/user/auth/google/callback` to the OAuth client.

After changing `/etc/pritis-dues/env`, run `sudo systemctl restart dpp-api dpp-worker`.

## 14. Staging, then live, then a pilot

1. **Staging (test keys).**
   - Set `ENVIRONMENT=staging` and `PAYSTACK_SECRET_KEY=sk_test_…`.
   - Run a full round with Paystack's test cards: a student pays → the receipt email arrives →
     settlement is marked → the rep requests a payout → it's approved → Paid.
   - Then request and approve a refund.
2. **Live.**
   - Set `ENVIRONMENT=production` and `PAYSTACK_SECRET_KEY=sk_live_…`.
   - Point Paystack's **live** webhook at the same URL.
   - Restart both services.
   - Delete the staging test data first by starting from a fresh database: drop and recreate
     `pritis_dues`, rerun step 6, and keep the backup from staging if you want it. Test payments
     can't be removed from the ledger otherwise.
3. **Pilot.**
   - One class pays one real due, then completes one real payout.
   - Check the nightly check (Overview → Nightly check) stays clean for a few days.
   - Then open it to all five classes.

## 15. Monitoring

- Add an uptime check on `https://dpp.pritis.name.ng/api/health`, e.g. UptimeRobot (free).
- The app already emails the accountant and super admin when the nightly check finds a mismatch
  and when a refund fails. A failed payout emails the rep and shows on Payouts.
- Logs: `journalctl -u dpp-api`, `-u dpp-worker`, and `/var/log/nginx/dpp*.log`. Nginx logs are
  rotated by the existing logrotate setup.
- Error tracking such as Sentry can be added later; it isn't wired in yet.

## Releasing an update

```bash
sudo /srv/pritis-dues/repo/pritis-payments/deploy/scripts/release.sh          # main branch
```

The script:

1. pulls the code and installs the requirements
2. **backs up**
3. migrates the database
4. restarts both services
5. waits for `/api/health`

If the health check fails, read `journalctl -u dpp-api -n 100`.

To roll back code without schema changes:
`sudo -u dpp git -C /srv/pritis-dues/repo checkout <previous commit>` and restart. A release that
migrated the database is rolled back by restoring the pre-release backup.

## Removing it completely

```bash
sudo systemctl disable --now dpp-api dpp-worker dpp-backup.timer dpp-restore-test.timer
sudo rm /etc/systemd/system/dpp-* /etc/nginx/sites-enabled/*dpp.pritis.name.ng.conf \
        /etc/nginx/sites-available/*dpp.pritis.name.ng.conf /etc/nginx/conf.d/dpp-ratelimit.conf \
        /etc/nginx/snippets/dpp-*.conf
sudo nginx -t && sudo systemctl reload nginx
```

pritis.name.ng is untouched throughout.
