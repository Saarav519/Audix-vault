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
