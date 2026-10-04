# Audix Vault

**Where Audits Meet Exceptionalism.** Audix Vault is the private client portal of Audix Solutions & Co. After each stock audit, Audix staff enter the category-wise summary, observations and files. Each client then signs in to see:

- every published audit: store, date, type, shift, category-wise stock, sale value, differences, auto remark and observations;
- the signoff copy and evidence photographs (preview and download), plus the audit Excel, variance report and scanned data (download only);
- a dashboard with daily, weekly, monthly and yearly summaries;
- a comparison of any two audits (value and percentage, category by category, with follow-ups and improvement points);
- a yearly audit aging report against the 90-day audit cycle, with Excel and PDF export.

The audit summary table is the single source of truth: every number on every screen, export and email is computed from the saved category lines by `core/calc.py`. Uploaded files are stored and downloaded, never parsed.

## Technology

Python 3.12, Django 5.2, PostgreSQL, server-rendered templates with HTMX, server-rendered SVG charts, ReportLab (PDF), openpyxl (Excel), private S3-compatible storage with presigned URLs, Argon2, django-axes, whitenoise and gunicorn.

| App | What it holds |
|---|---|
| `core` | `calc.py` (all business rules), `formatting.py` (₹, Indian grouping, L/Cr), `storage.py` (S3 / local), `charts.py`, exports, scoping, backups, `seed_demo` |
| `accounts` | user model (admin, auditor, client, client store manager), sign-in, first-login password change, `bootstrap_admin` |
| `clients` | clients, stores, categories, the admin console (clients, logins, settings, view as client) |
| `audits` | audits, lines, observations, follow-ups, files; add/edit audit, audit detail, uploads, ZIP, import |
| `reports` | dashboard, all audits, tracker, compare, aging report, exports, admin overview |
| `activity` | append-only activity log and its page |
| `notifications` | branded emails and their cron commands |

## Run it

### In a Claude Code cloud session or on a Linux machine

```bash
make db        # starts PostgreSQL 16 and creates role/database audix/audix (needs root)
make setup     # creates .venv, installs requirements-dev.txt, copies .env.example to .env
# edit .env: set DEBUG=1, ADMIN_LOGIN_ID / ADMIN_INITIAL_PASSWORD, DEMO_CLIENT_PASSWORD
make seed      # optional: Greenfield Retail and Urban Mart demo data
make run       # migrate, create the first admin, runserver on :8000
make test      # pytest
make lint      # ruff
make check     # manage.py check --deploy with production-like variables
```

### Anywhere else

Install Python 3.12 and PostgreSQL, create a database, then:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # and edit it
.venv/bin/python manage.py migrate
.venv/bin/python manage.py bootstrap_admin
.venv/bin/python manage.py runserver
```

Tests need no internet: S3 is faked with `moto` and email uses Django's in-memory backend.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | yes in production | dev key when `DEBUG=1` | Django secret key |
| `DEBUG` | no | `0` | `1` only for local development |
| `DATABASE_URL` | yes | local `audix` database | PostgreSQL URL |
| `TIME_ZONE` | no | `Asia/Kolkata` | Display time zone (timestamps are stored in UTC) |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | no | — | Comma separated. `RAILWAY_PUBLIC_DOMAIN` is added automatically |
| `PORTAL_BASE_URL` | no | `https://$RAILWAY_PUBLIC_DOMAIN` | Used for links in emails |
| `ADMIN_LOGIN_ID`, `ADMIN_INITIAL_PASSWORD` | first deploy | — | `bootstrap_admin` creates this admin if no admin exists |
| `ADMIN_NAME`, `ADMIN_EMAIL` | no | `Audix Admin` | First admin's details |
| `ADMIN_FORCE_PASSWORD_CHANGE` | no | `1` | `0` skips the forced change at first login |
| `ADMIN_RESET_PASSWORD` | no | — | Recovery only. `1` resets the password of the admin named by `ADMIN_LOGIN_ID` to `ADMIN_INITIAL_PASSWORD` on the next start (see "Forgot the admin password"). Delete it afterwards |
| `ALLOW_DEMO_SEED` | no | `0` | `1` lets `seed_demo` run when `DEBUG=0` |
| `DEMO_CLIENT_PASSWORD` | for demo data | — | Password of the demo logins `greenfield` and `urbanmart` |
| `DEMO_AUDITOR_PASSWORD` | no | — | Creates a demo auditor login `auditor` |
| `S3_ENDPOINT_URL` | no | — | S3-compatible endpoint (Railway Bucket, Cloudflare R2). Empty for AWS S3 |
| `S3_BUCKET_NAME` | for files | — | Private bucket for audit files. Turns S3 storage on |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION` | with the bucket | region `auto` with an endpoint, else `ap-south-1` | Bucket credentials |
| `S3_ADDRESSING_STYLE` | no | `path` with an endpoint, else `virtual` | S3 addressing style |
| `S3_UPLOAD_MODE` | no | `proxy` | `proxy`: browsers upload to the app, which streams files into the bucket (works with Railway Buckets and any bucket without CORS rules). `direct`: browsers upload straight to the bucket with a presigned POST (needs a bucket CORS rule allowing `POST` from the portal domain). Downloads always use 10-minute presigned links |
| `SIGNED_URL_MINUTES` | no | `10` | Default file-link lifetime (also editable in Settings) |
| `ALLOW_LOCAL_STORAGE` | no | `0` | `1` stores files on local disk (development only; Railway disk is not permanent) |
| `EMAIL_URL` | no | console output | e.g. `smtp+tls://user:pass@smtp.example.com:587` |
| `DEFAULT_FROM_EMAIL` | no | `Audix Vault <no-reply@audix.local>` | Sender |
| `SENTRY_DSN` | no | — | Error monitoring (personal data is scrubbed) |
| `BACKUP_S3_ENDPOINT_URL`, `BACKUP_S3_BUCKET_NAME`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`, `BACKUP_S3_REGION` | for backups | — | A separate bucket for database and file backups |
| `PDF_FONT_PATH` | no | bundled DejaVu Sans | TTF font for PDFs. DejaVu Sans and DejaVu Sans Bold are bundled in `core/fonts/` (free licence in `LICENSE-DejaVu.txt`), so the ₹ sign prints on Railway. Set this only to use another font that has the ₹ glyph |
| `LOG_LEVEL` | no | `INFO` | Logs go to stdout |

Without a bucket the app still works: upload areas say "File storage is not configured yet".

## Deploy on Railway

1. Create a Railway project from this GitHub repository.
2. Add a **PostgreSQL** service to the project.
3. On the web service, add these variables:
   ```
   SECRET_KEY             = (long random string)
   DEBUG                  = 0
   DATABASE_URL           = ${{Postgres.DATABASE_URL}}
   TIME_ZONE              = Asia/Kolkata
   ADMIN_LOGIN_ID         = admin
   ADMIN_INITIAL_PASSWORD = (strong temporary password; you must change it at first login)
   ALLOW_DEMO_SEED        = 1          (first deploy only)
   DEMO_CLIENT_PASSWORD   = (password for the demo logins greenfield and urbanmart)
   ALLOW_LOCAL_STORAGE    = 1          (only until the bucket variables are added)
   ```
4. Generate a public domain (Settings → Networking). Deploy. `railway.json` runs migrations, collects static files, creates the first admin, seeds the demo when allowed, and starts gunicorn (2 workers, 120-second timeout). The health check is `/healthz`.
5. Open the domain and sign in with `ADMIN_LOGIN_ID`.
6. Later: add a bucket and set `S3_ENDPOINT_URL`, `S3_BUCKET_NAME`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`; remove `ALLOW_LOCAL_STORAGE`. Leave `S3_UPLOAD_MODE` unset (`proxy`) for a Railway Bucket: uploads pass through the app, so the bucket needs no CORS setup; gunicorn's timeout is 120 seconds so a 50 MB file has time to finish on a slow connection. Then add `EMAIL_URL` and `DEFAULT_FROM_EMAIL`; remove `ALLOW_DEMO_SEED` (the demo data stays until you delete it or run `seed_demo --reset` with seeding allowed).

### Cron jobs (Railway cron services running the same image)

| Schedule (UTC) | Command |
|---|---|
| `30 21 * * *` (03:00 IST daily) | `python manage.py backup_database` |
| `45 21 * * *` | `python manage.py sync_bucket_backup` |
| `0 3 * * *` (08:30 IST daily) | `python manage.py send_missing_file_reminders` |
| `0 3 1 * *` (1st of the month) | `python manage.py send_monthly_summaries` |

Restore with `python manage.py restore_database --yes` (latest backup) or `--key db/audix-YYYYMMDD-HHMMSS.sql.gz`.

### Forgot the admin password

1. In Railway, open the web service → **Variables**.
2. Set `ADMIN_RESET_PASSWORD` = `1`, and set `ADMIN_INITIAL_PASSWORD` to a new strong temporary password. `ADMIN_LOGIN_ID` must be the admin's login ID (any letter case).
3. Redeploy. On start, `bootstrap_admin` sets that admin's password, re-enables the login, clears any sign-in lockout and asks for a new password at the next sign-in. The log shows `bootstrap_admin: password reset for '<login id>'`; the password is never printed.
4. Sign in with the temporary password and choose a new one.
5. **Delete `ADMIN_RESET_PASSWORD`** from Variables. While it is set, every restart resets the password again.

It only works for a login with the Admin role; client and auditor passwords are reset from the console.

## First client

1. Sign in as admin → **Clients** → **Create client**.
2. Enter the company name, contact emails, a login ID and a temporary password (**Generate** makes one), an optional logo, the stores (`code, name, city` per line) and the categories (the default list is filled in).
3. Share the login ID and temporary password with the client. They choose a new password at first sign-in.
4. **Add audit** → pick the client → fill the six steps → **Publish**. A store that is not in the list yet can be added on the spot: choose **+ Add new store...** in the Store dropdown (name required; city and code optional, a blank code becomes the next free S001, S002, ...). The client gets a "New audit" email if notifications are on.

Use **View as client** on a client's page to see exactly what they see (read-only, bannered and logged).

## Demo data

`python manage.py seed_demo` creates **Greenfield Retail** (8 stores, 7 categories, quarterly full audits since 1 April 2024 with some delays, two stores currently overdue, frequent cycle counts, one store without sale values, placeholder files) and **Urban Mart Pvt Ltd** (3 stores). Logins: `greenfield` and `urbanmart` with `DEMO_CLIENT_PASSWORD`. It only runs when `DEBUG=1` or `ALLOW_DEMO_SEED=1`, is idempotent, and `--reset` recreates the demo clients. `--if-allowed` makes it a no-op when seeding is off.

## Old data

Admin → **Import old data**: download the template, fill one row per audit per category, run a dry run to see row-level errors, then upload again with the dry run unticked.

## Project files

- `DECISIONS.md`: choices made where the brief left room.
- `STATUS.md`: what is done, partly done or not done.
- `PROGRESS.md`: build stages checklist.
