# Decisions

One line each: what, and why.

- Python 3.12 venv from `/usr/bin/python3.12`; dependencies pinned in `requirements.txt` from the versions installed in the build session.
- Django 5.2 LTS (latest 5.x). Why: long-term support until 2028.
- No Django admin site; all admin screens are custom console pages. Why: one consistent UI and permission layer.
- `User.login_id` is stored lower-case and unique, so login IDs are case-insensitive.
- Client users who hit admin URLs get 404 (not 403) so admin URLs are not discoverable; auditors get 403 on admin-only pages.
- Data isolation lives in `AuditQuerySet.for_scope()` plus `core.scoping.scope_for()`; every client-facing query starts there.
- Business numbers are computed in `core/calc.py` with `Decimal`; the audit stores a snapshot of totals, status and remark, refreshed on every save and whenever thresholds or the cycle change (recompute on change, not on read).
- Temporary passwords are shown once in the HTTP response (not via Django messages, which could store them in a cookie).
- The `.env` file is ignored by the test settings (`AUDIX_SKIP_DOTENV`) so tests never depend on local values.
- Associated-with mark: `assets/` was not supplied, so a text chip "Associated with CA India | Vikas Kshitij & Associates, Chartered Accountants" is used; the x mark is the inline SVG from the brief.
- `reference/audix-portal-demo.html` was not in the repo, so screens are built from the brief's design tokens and descriptions.
- Health check path is exempt from the HTTPS redirect and `healthcheck.railway.app` is allowed automatically on Railway, so health checks do not loop.
- Photos: GPS location is removed from the stored file on upload (other EXIF, including the date, is kept), so location can never reach clients. HEIC is converted to JPEG when `pillow-heif` is installed, otherwise HEIC is refused at upload.
- Photo thumbnails are streamed through Django after a permission check (no presigned URL per thumbnail, so the activity log is not flooded); originals and every download use 10-minute presigned links that are logged.
- File content type is decided by the server from the extension, never trusted from the browser; the presigned POST enforces it.
- ZIPs are streamed with `ZIP_STORED` (photos and Office files are already compressed) and capped at 500 MB (`MAX_ZIP_BYTES`).
- Local storage (development only) uses Django-signed upload/download links that expire like presigned URLs and still need a signed-in session.
- An auditor who presses Publish without permission gets the audit saved as a draft with a message, instead of losing the work.
- Dashboard shortage/excess split is at audit level (an audit with a net negative difference counts as shortage), matching the audit totals rule.
- Dashboard default period is Monthly (last 90 days); weekly buckets are 7-day blocks ending today, monthly buckets are calendar months.
- Staff can open every portal screen with a client picker (`?client=`); clients never see the picker and their client is fixed by their login.
- "Last audit to latest" in store health uses the store's latest audit in the window and that audit's previous audit in the same series; Better/Worse needs a 0.15-point move, otherwise "About the same".
- Aging "Net difference" uses the sale base over the audits that have a sale value when any audit in the period has one, otherwise the stock base.
- PDFs use DejaVu Sans when it is installed (it has the ₹ and − glyphs); otherwise Helvetica with "Rs" and "-" so a PDF always renders. `PDF_FONT_PATH` can point at any TTF.
- Excel money cells use an Indian-grouping number format for positive values and plain grouping with a leading minus for negatives (Excel conditional formats cannot do both in one format).
- "Waiting for files" means a live audit from the last 180 days without a signoff copy, photographs, the audit Excel or the variance report (scanned data is optional).
- Stores above the Watch threshold on the Overview are judged on each store's latest published audit.
- Backups go to a separate bucket (`BACKUP_S3_*`): `backup_database` (pg_dump, gzip, `db/`), `sync_bucket_backup` (`files/`), `restore_database --yes`.
