"""Demo data: Greenfield Retail (8 stores) and Urban Mart (3 stores).

Runs only when DEBUG=1 or ALLOW_DEMO_SEED=1. Idempotent; --reset recreates the demo clients.
"""

from __future__ import annotations

import io
import os
import random
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, User
from audits import services
from audits.models import (
    Audit,
    AuditFile,
    AuditLine,
    AuditStatus,
    FileKind,
    FollowUp,
    Observation,
)
from clients.models import Category, Client, Store
from core import calc, storage

CATEGORIES = [  # name, share of stock, price factor, damage multiplier, WBC multiplier
    ("Grocery & staples", .28, .8, 1, .7),
    ("Dairy & frozen", .10, .9, 1.8, .5),
    ("Beverages", .14, 1, 1.2, .9),
    ("Snacks & confectionery", .14, .9, 1, 1.2),
    ("Personal care", .12, 1.5, .6, 1.5),
    ("Home care", .10, 1.2, .6, 1),
    ("General merchandise", .12, 1.3, .5, 1.6),
]
GREENFIELD = [  # code, name, city, price, base units, bias, trend, problems
    ("GR01", "Andheri West", "Mumbai", 118, 21000, .0055, 0, [1, .8, 1, 1.1, 1, 1, 1]),
    ("GR02", "Kothrud", "Pune", 96, 16500, .0045, 0, [1, 1, 1, 1, .9, 1.1, 1]),
    ("GR03", "Saket", "Delhi", 132, 24000, .021, -.35, [.8, .5, 2.6, 1, 1.9, .9, 1]),
    ("GR04", "Sector 29", "Gurugram", 124, 19500, .0125, 0, [.8, 1, 1.6, 1, .9, 2.3, 1]),
    ("GR05", "Indiranagar", "Bengaluru", 109, 20500, .0032, 0, [1, 1, 1, .9, 1, 1, 1.1]),
    ("GR06", "Banjara Hills", "Hyderabad", 121, 18000, .0165, 0, [.7, .9, 1, 1, 2.4, 1, 2]),
    ("GR07", "Satellite", "Ahmedabad", 88, 15500, .008, 0, [1.1, 1, .9, 1.2, 1, 1, 1]),
    ("GR08", "Malviya Nagar", "Jaipur", 92, 14500, .0245, .3, [1.5, .8, .8, 2.8, 1, 1, .8]),
]
URBAN = [
    ("UM01", "Viman Nagar", "Pune", 104, 12000, .009, 0, [1] * 7),
    ("UM02", "Salt Lake", "Kolkata", 98, 11000, .014, 0, [1.2, 1, 1, 1.4, 1, 1, 1]),
    ("UM03", "Anna Nagar", "Chennai", 112, 13500, .006, 0, [1] * 7),
]
NO_SALE_STORES = {"GR07"}
OVERDUE = {"GR04": 12, "GR08": 27}  # stores whose next full audit is past the cycle today
OTHER_TYPES = ["surprise", "cycle", "closing"]
SHIFTS = ["morning", "afternoon", "night"]
START = date(2024, 4, 1)


def normalise(mults):
    mean = sum(m * c[1] for m, c in zip(mults, CATEGORIES, strict=True))
    return [m / mean for m in mults]


def money(v) -> Decimal:
    return Decimal(str(round(v, 2)))


def units(v) -> Decimal:
    return Decimal(max(0, int(round(v))))


class Command(BaseCommand):
    help = "Create the Greenfield Retail and Urban Mart demo data (only when DEBUG=1 or ALLOW_DEMO_SEED=1)."

    def add_arguments(self, parser):
        parser.add_argument("--if-allowed", action="store_true", help="Do nothing (quietly) when seeding is not allowed")
        parser.add_argument("--reset", action="store_true", help="Delete and recreate the demo clients")
        parser.add_argument("--no-files", action="store_true", help="Skip placeholder photos and files")

    def handle(self, *args, **opts):
        allowed = settings.DEBUG or settings.ALLOW_DEMO_SEED
        if not allowed:
            if opts["if_allowed"]:
                return
            raise CommandError("Demo seeding is off. Set DEBUG=1 or ALLOW_DEMO_SEED=1.")
        password = os.environ.get("DEMO_CLIENT_PASSWORD", "")
        if not password:
            msg = "seed_demo: DEMO_CLIENT_PASSWORD is not set, skipping demo data."
            if opts["if_allowed"]:
                self.stdout.write(msg)
                return
            raise CommandError(msg)
        exists = Client.objects.filter(slug="greenfield-retail").exists()
        if exists and not opts["reset"]:
            self.stdout.write("seed_demo: demo data already present (use --reset to recreate).")
            return
        if opts["reset"]:
            self.reset()
        self.rng = random.Random(2026)
        self.today = timezone.localdate()
        self.admin = User.objects.filter(role=Role.ADMIN).order_by("created_at").first()
        self.backend = None if opts["no_files"] else storage.get_backend()
        self.placeholders = {}
        with transaction.atomic():
            green = self.make_client("Greenfield Retail", "greenfield-retail", "accounts@greenfield.example",
                                     GREENFIELD, "greenfield", password)
            urban = self.make_client("Urban Mart Pvt Ltd", "urban-mart", "audit@urbanmart.example", URBAN,
                                     "urbanmart", password, start=self.today - timedelta(days=400))
        auditor_pw = os.environ.get("DEMO_AUDITOR_PASSWORD", "")
        if auditor_pw and not User.objects.filter(login_id="auditor").exists():
            User.objects.create_user("auditor", auditor_pw, name="Demo Auditor", role=Role.AUDITOR,
                                     must_change_password=False)
        n = Audit.objects.filter(client__in=[green, urban]).count()
        self.stdout.write(self.style.SUCCESS(f"seed_demo: created Greenfield Retail and Urban Mart ({n} audits)."))

    # ------------------------------------------------------------ setup

    def reset(self):
        demo = Client.objects.filter(slug__in=["greenfield-retail", "urban-mart"])
        audits = Audit.objects.filter(client__in=demo)
        backend = storage.get_backend()
        if backend is not None:
            for f in AuditFile.objects.filter(audit__in=audits):
                for key in (f.storage_key, f.thumbnail_key):
                    if key:
                        try:
                            backend.delete(key)
                        except Exception:
                            pass
        FollowUp.objects.filter(audit__in=audits).delete()
        audits.update(previous_audit=None)
        audits.delete()
        User.objects.filter(client__in=demo).delete()
        demo.delete()

    def make_client(self, name, slug, email, store_rows, login_id, password, start=START):
        client = Client.objects.create(name=name, slug=slug, contact_emails=email, created_by=self.admin,
                                       notify_on_publish=False)
        cats = [Category.objects.create(client=client, name=c[0], sort_order=(i + 1) * 10)
                for i, c in enumerate(CATEGORIES)]
        if User.objects.filter(login_id=login_id).exists():
            User.objects.filter(login_id=login_id).delete()
        User.objects.create_user(login_id, password, name=f"{name} head office", email=email, role=Role.CLIENT,
                                 client=client, must_change_password=False)
        th = client.thresholds()
        for i, row in enumerate(store_rows):
            code, sname, city, price, base, bias, trend, problems = row
            store = Store.objects.create(client=client, code=code, name=sname, city=city)
            self.seed_store(client, store, cats, i, price, base, bias, trend, normalise(problems), th, start)
        return client

    # ------------------------------------------------------------ audits

    def schedule(self, index, start, overdue_by=None):
        rng = self.rng
        full = []
        d = start + timedelta(days=index * 9 + rng.randint(0, 5))
        last_allowed = self.today - timedelta(days=90 + overdue_by) if overdue_by else self.today
        while d <= self.today:
            if d > last_allowed:
                break
            full.append(d)
            if rng.random() < 0.55:
                d += timedelta(days=rng.randint(84, 90))
            else:
                d += timedelta(days=91 + int(rng.random() ** 2 * 40))
        other = []
        d = start + timedelta(days=rng.randint(3, 12))
        full_set = set(full)
        while d <= self.today:
            if d not in full_set:
                other.append((d, rng.choice(OTHER_TYPES)))
            d += timedelta(days=rng.randint(8, 16))
        # Seed up to today: every store gets a frequent audit in the last 7 days so the Daily view has data.
        week_start = self.today - timedelta(days=6)
        if not any(day >= week_start for day, _ in other):
            taken = full_set | {day for day, _ in other}
            days = [self.today - timedelta(days=back) for back in rng.sample(range(7), 7)]
            free = [day for day in days if day not in taken]
            spaced = [day for day in free if not other or (day - other[-1][0]).days >= 3]
            if spaced or free:
                other.append(((spaced or free)[0], rng.choice(OTHER_TYPES)))
        return full, other

    def seed_store(self, client, store, cats, index, price, base, bias, trend, mults, th, start):
        full, other = self.schedule(index, start, OVERDUE.get(store.code))
        plan = [(d, "physical") for d in full] + other
        plan.sort()
        span = max((self.today - start).days, 1)
        last_by_series = {}
        created = []
        for d, audit_type in plan:
            series = calc.series_for(audit_type)
            progress = (d - start).days / span
            trend_factor = max(0.2, 1 + trend * (progress - 0.5) * 2)
            scale = 1.0 if series == "full" else self.rng.uniform(0.3, 0.45)
            audit = Audit.objects.create(
                client=client, store=store, audit_date=d, audit_type=audit_type, shift=self.rng.choice(SHIFTS),
                status=AuditStatus.DRAFT, created_by=self.admin,
            )
            lines = self.make_lines(audit, cats, price, base * scale, bias * trend_factor, mults)
            AuditLine.objects.bulk_create(lines)
            stock_total = sum(ln.stock_value for ln in lines)
            prev_date = last_by_series.get(series)
            if prev_date and store.code not in NO_SALE_STORES and self.rng.random() > 0.05:
                turnover = self.rng.uniform(1.0, 1.4)
                audit.sale_value = money(float(stock_total) * turnover * (d - prev_date).days / 30)
            last_by_series[series] = d
            audit.status = AuditStatus.PUBLISHED
            audit.published_by = self.admin
            audit.published_at = timezone.now()
            audit.save()
            audit.refresh_snapshot(th)
            drafts = calc.suggest_drafts(audit.pk, audit.numbers_by_category())
            by_name = {c.name: c for c in cats}
            for i, dr in enumerate(drafts):
                Observation.objects.create(audit=audit, category=by_name.get(dr["category"]) if dr["category"] else None,
                                           kind=dr["kind"], severity=dr["severity"], text=dr["text"],
                                           recommendation=dr["recommendation"], sort_order=i)
            created.append(audit)
        for series in ("full", "other"):
            services.relink_series(client, store.pk, series)
        # follow-ups from the comparison rule
        for audit in Audit.objects.filter(pk__in=[a.pk for a in created]).select_related("previous_audit", "store"):
            prev = audit.previous_audit
            if prev is None:
                continue
            cmp = services.comparison_for(prev, audit)
            rows = [FollowUp(audit=audit, previous_observation_id=item.observation.id,
                             status=calc.FOLLOWUP_PRESET.get(item.status, "in_progress"))
                    for item in cmp.followups]
            FollowUp.objects.bulk_create(rows)
        if self.backend is not None:
            recent = [a for a in created if (self.today - a.audit_date).days <= 200]
            for a in recent:
                if self.rng.random() < 0.08:
                    continue  # leave a few for "Waiting for files"
                self.attach_files(a)

    def make_lines(self, audit, cats, price, base_units, bias, mults):
        rng = self.rng
        roll = rng.random()
        matched = roll < 0.05
        excess = 0.05 <= roll < 0.12
        out = []
        for cat, (_name, share, pfactor, dmult, wmult), m in zip(cats, CATEGORIES, mults, strict=True):
            stock_qty = units(base_units * share * rng.uniform(0.9, 1.1))
            unit_price = price * pfactor * rng.uniform(0.97, 1.03)
            stock_value = money(float(stock_qty) * unit_price)
            if matched:
                rate = 0.0
            elif excess:
                rate = -rng.uniform(0.0005, 0.004)
            else:
                rate = bias * rng.uniform(0.35, 1.65) * m * rng.uniform(0.5, 1.5)
            diff_units = -int(round(float(stock_qty) * rate))
            total_qty = stock_qty + diff_units
            damage_qty = units(float(stock_qty) * rng.uniform(0.001, 0.006) * dmult) if not matched else Decimal(0)
            wbc_qty = units(float(stock_qty) * rng.uniform(0.0005, 0.004) * wmult) if not matched else Decimal(0)
            damage_qty = min(damage_qty, total_qty)
            wbc_qty = min(wbc_qty, total_qty - damage_qty)
            physical_qty = total_qty - damage_qty - wbc_qty
            total_value = stock_value if matched else money(float(stock_value) * (1 - rate) * rng.uniform(0.999, 1.001))
            damage_value = money(float(damage_qty) * unit_price * rng.uniform(0.9, 1.1))
            wbc_value = money(float(wbc_qty) * unit_price * rng.uniform(0.9, 1.1))
            physical_value = max(Decimal(0), total_value - damage_value - wbc_value)
            line = AuditLine(audit=audit, category=cat, stock_qty=stock_qty, stock_value=stock_value,
                             physical_qty=physical_qty, physical_value=physical_value, damage_qty=damage_qty,
                             damage_value=damage_value, wbc_qty=wbc_qty, wbc_value=wbc_value)
            n = calc.Numbers.from_mapping(line)
            line.total_physical_qty, line.total_physical_value = n.total_physical_qty, n.total_physical_value
            line.diff_qty, line.diff_value = n.diff_qty, n.diff_value
            out.append(line)
        return out

    # ------------------------------------------------------------ files

    def placeholder(self, kind, label):
        from PIL import Image, ImageDraw

        if kind == "photo":
            img = Image.new("RGB", (800, 600), (18, 23, 15))
            draw = ImageDraw.Draw(img)
            draw.polygon([(0, 0), (240, 0), (480, 300), (240, 600), (0, 600), (240, 300)], fill=(185, 225, 12))
            draw.text((500, 280), label, fill=(232, 237, 226))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=70)
            thumb = io.BytesIO()
            img.resize((480, 360)).save(thumb, "JPEG", quality=70)
            return out.getvalue(), thumb.getvalue()
        if kind == "signoff":
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas

            out = io.BytesIO()
            c = canvas.Canvas(out, pagesize=A4)
            c.setFont("Helvetica-Bold", 16)
            c.drawString(60, 760, "Stock audit signoff (demo)")
            c.setFont("Helvetica", 11)
            c.drawString(60, 730, label)
            c.drawString(60, 690, "Store manager: ____________________     Auditor: ____________________")
            c.showPage()
            c.save()
            return out.getvalue(), None
        from openpyxl import Workbook

        wb = Workbook()
        wb.active.append(["Demo file", label])
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue(), None

    def put(self, audit, kind, name, data, ct, thumb=None, order=1):
        key = storage.build_key(audit.client_id, audit.pk, kind, name)
        self.backend.put(key, data, ct)
        thumb_key = ""
        if thumb:
            thumb_key = key.rsplit("/", 1)[0] + f"/thumbs/{os.urandom(8).hex()}.jpg"
            self.backend.put(thumb_key, thumb, "image/jpeg")
        AuditFile.objects.create(audit=audit, kind=kind, storage_key=key, original_name=name, content_type=ct,
                                 size_bytes=len(data), thumbnail_key=thumb_key, uploaded_by=self.admin, sort_order=order)

    def attach_files(self, audit):
        label = f"{audit.store.code} {audit.store.name} · {calc.AUDIT_TYPE_LABELS[audit.audit_type]} · {audit.reference}"
        pdf, _ = self.placeholder("signoff", label)
        self.put(audit, FileKind.SIGNOFF, f"{audit.reference}-signoff.pdf", pdf, "application/pdf")
        xlsx, _ = self.placeholder("excel", label)
        ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        self.put(audit, FileKind.AUDIT_EXCEL, f"{audit.reference}-audit.xlsx", xlsx, ct)
        self.put(audit, FileKind.VARIANCE_REPORT, f"{audit.reference}-variance.xlsx", xlsx, ct)
        if audit.is_full:
            self.put(audit, FileKind.SCANNED_DATA, f"{audit.reference}-scan.pdf", pdf, "application/pdf")
        for i in range(1, self.rng.randint(2, 4) + 1):
            img, thumb = self.placeholder("photo", f"{audit.store.code} photo {i}")
            self.put(audit, FileKind.PHOTO, f"{audit.reference}-photo-{i}.jpg", img, "image/jpeg", thumb, i)
