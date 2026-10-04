# Status

Honest state of the build against the brief. Test suite: 125 tests (`pytest -q`), `ruff check .` clean, `manage.py check --deploy` shows no errors (one warning left on purpose: HSTS include-subdomains is an owner decision for the final domain), `pip-audit` finds no known vulnerabilities.

## Part B

| Section | Status | Notes |
|---|---|---|
| 1 Product summary | Done | |
| 2 Roles and permissions | Done | Admin, auditor, client, client store manager (role, store limits and the "Add a login" form). Isolation lives in one place (`AuditQuerySet.for_scope`). |
| 3 Branding and design | Done | Tokens, fonts, light/dark with toggle, bottom tab bar under 860px. `reference/` and `assets/` were not in the repo, so the look follows the written spec; the associated-with mark is a text chip. |
| 4 Glossary | Done | |
| 5.1 to 5.7 Numbers, status, remark, previous audit, aging | Done | `core/calc.py`, acceptance tests 1 to 9. Status and remark are recomputed for a client when thresholds change. |
| 5.8 Comparison | Done | Overall rows, category table, verdicts, sentences, follow-ups, improvement points, paired bar chart. |
| 5.9 Observations and drafts | Mostly done | Templates can be edited in the database (`ObservationTemplate`), but there is no screen for editing them yet. |
| 5.10 Follow-up in the entry form | Done | Preset from the numbers, saved per audit, shown to clients. |
| 5.11 Dashboard | Done | DB aggregation; verified with 20,000 extra audits (16 queries, 0.4 s for the yearly view). |
| 5.12 Tracker | Done | Calendar, 90-day timeline, next full audit due. |
| 5.13 Aging report | Done | FY and calendar years, store filter, admin client picker, Excel and PDF export. |
| 5.14 Compare page | Done | |
| 6 Screens: client portal | Done | Audit detail opens as a slide-in panel on desktop and as a page on mobile. |
| 6 Screens: admin console | Done | Overview, clients, add audit, aging, compare, activity log, settings (thresholds, cycle, toggles, session timeout, link expiry, backup status), team logins. |
| 7 Add audit | Done | Six steps, live server preview, paste from Excel, warnings (duplicate, zero physical, unusual sale value), draft/publish, edit trail, soft delete. Admins and auditors can add a missing store from the Store dropdown ("+ Add new store...") without leaving the page; duplicates by code or by name and city are refused, and each addition is logged. |
| 8 Data model | Done | UUID keys, Decimal fields, the listed indexes. |
| 9 Files and storage | Done | Presigned upload with confirm, type and size rules, HEIC to JPEG (needs the `pillow-heif` wheel), GPS removed from photos, thumbnails, 10-minute logged links, ZIP streaming with a 500 MB cap. |
| 10 Security | Done | Argon2, 10-character minimum, axes lockout, idle timeout, secure cookies, HSTS, CSP, CSRF, isolation tests, logged view-as-client. Sentry is wired but not tested against a real DSN. |
| 11 Exports and emails | Done | Excel and PDF with letterhead; new audit, monthly summary and missing-files emails. PDFs show ₹ when DejaVu Sans is installed, otherwise "Rs". |
| 12 Import of old data | Done | Template, dry run, row-level errors, linking by date. |
| 13 Non-functional | Mostly done | Performance measured as above. Accessibility follows the rules (labels, focus, text status labels, reduced motion), but no automated accessibility audit was run. |
| 14 Technology | Done | As in Part C, C1. |
| 15 Acceptance tests | Done | 1 to 22 are covered in `tests/`. |
| 16 Out of scope | Not built, as asked | |

## Part C stages

| Stage | Status | Notes |
|---|---|---|
| 0 Foundation | Done | |
| 1 Calculation core | Done | |
| 2 Accounts, security, clients | Done | |
| 3 Audit entry and publishing | Done | |
| 4 Files and audit detail | Done | Tested with moto (S3) and the local backend, and in headless Chromium (live preview, upload, publish, lightbox). |
| 5 Client portal | Done | |
| 6 Admin extras, exports, email, backups | Done | Backups need a separate bucket (`BACKUP_S3_*`) and cron services on Railway. |
| 7 Import and polish | Done | Responsive and dark mode checked with screenshots at 1366px and 390px. |
| 8 Hardening and delivery | Done | Fresh-database deploy simulated with gunicorn, `DEBUG=0` and the Railway start command: health check 200, admin created, demo seeded, HTTPS redirect without loops. |

## Known gaps and items for the owner

- No editing screen for the observation draft templates (the first item on the brief's cut list).
- Google Fonts could not load in the build session, so screenshots used fallback fonts; production loads Montserrat and IBM Plex Sans normally.
- With `ALLOW_LOCAL_STORAGE=1` on Railway, files are lost on redeploy. Add a bucket before real use.
- Activity log rows are never deleted (retention is more than 12 months by design); add a purge job if a limit is wanted.
- To confirm before go-live: a clean logo file, written permission to show the CA India mark and the Vikas Kshitij & Associates name, the production domain, the email sender and provider, each client's real category list, and a sample of real audit data for acceptance testing.
