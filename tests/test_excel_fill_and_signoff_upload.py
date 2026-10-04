"""Step 2 Excel template and upload; signoff upload on the audit page; missing-file preview."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as HttpClient
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from audits.models import AuditFile
from core import storage
from tests.test_files import BUCKET, s3, setup, upload  # noqa: F401  (fixtures)

pytestmark = pytest.mark.django_db
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def http_for(user):
    h = HttpClient()
    h.force_login(user)
    return h


def xlsx(rows, name="filled.xlsx"):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue(), content_type=XLSX)


HEAD = ["Category", "Stock qty", "Stock value", "Physical qty", "Physical value", "Damage qty", "Damage value",
        "WBC qty", "WBC value"]


# ---------------------------------------------------------------- Excel template and upload


def test_template_lists_client_categories(admin, client_a):
    r = http_for(admin).get(reverse("audits:lines_template") + f"?client={client_a.pk}")
    assert r.status_code == 200 and r["Content-Type"] == XLSX
    ws = load_workbook(io.BytesIO(r.content)).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    assert list(rows[0]) == HEAD
    assert [row[0] for row in rows[1:]] == [c.name for c in client_a.categories.filter(is_active=True)]


def test_upload_fills_matching_categories(admin, client_a):
    cats = list(client_a.categories.filter(is_active=True))
    rows = [["Audix template"], HEAD,  # header need not be the first row
            [cats[0].name.upper(), 1000, "1,00,000", 990, 99000.5, 2, 200, None, None],
            [cats[1].name, 500, 60000, 495, 59400, 0, 0, 1.25, 150],
            ["Not a category", 1, 1, 1, 1, 0, 0, 0, 0],
            ["Total", 1500, 160000, 1485, 158400, 2, 200, 1, 150]]
    r = http_for(admin).post(reverse("audits:lines_upload"), {"client": str(client_a.pk), "file": xlsx(rows)})
    assert r.status_code == 200, r.content
    j = r.json()
    v0, v1 = j["values"][str(cats[0].pk)], j["values"][str(cats[1].pk)]
    assert v0 == {"stock_qty": "1000", "stock_value": "100000", "physical_qty": "990", "physical_value": "99000.5",
                  "damage_qty": "2", "damage_value": "200", "wbc_qty": "", "wbc_value": ""}
    assert v1["wbc_qty"] == "1.25" and v1["wbc_value"] == "150"
    assert j["unknown"] == ["Not a category"] and not j["errors"]
    assert j["matched"] == [cats[0].name, cats[1].name]


def test_upload_columns_in_any_order_and_errors(admin, client_a):
    cats = list(client_a.categories.filter(is_active=True))
    head = ["Stock value", "Category"] + HEAD[1:2] + HEAD[3:]
    rows = [head, [100, cats[0].name, 10, 9, 90, 0, 0, 0, 0], ["abc", cats[1].name, -5, 0, 0, 0, 0, 0, 0]]
    j = http_for(admin).post(reverse("audits:lines_upload"), {"client": str(client_a.pk), "file": xlsx(rows)}).json()
    assert j["values"][str(cats[0].pk)]["stock_value"] == "100"
    assert any("Stock value is not a number" in e for e in j["errors"])
    assert any("Stock qty must be zero or more" in e for e in j["errors"])


def test_upload_rejects_bad_files(admin, client_a):
    h = http_for(admin)
    r = h.post(reverse("audits:lines_upload"),
               {"client": str(client_a.pk), "file": SimpleUploadedFile("x.xls", b"old", content_type="application/vnd.ms-excel")})
    assert r.status_code == 400 and ".xlsx" in r.json()["errors"][0]
    r = h.post(reverse("audits:lines_upload"), {"client": str(client_a.pk), "file": xlsx([["a", "b"], [1, 2]])})
    assert r.status_code == 400 and "Category" in r.json()["errors"][0]
    r = h.post(reverse("audits:lines_upload"), {"client": str(client_a.pk), "file": xlsx([HEAD[:3]])})
    assert "missing" in r.json()["errors"][0]


def test_upload_and_template_are_staff_only(user_a, client_a):
    h = http_for(user_a)
    assert h.get(reverse("audits:lines_template") + f"?client={client_a.pk}").status_code in (302, 403, 404)
    r = h.post(reverse("audits:lines_upload"), {"client": str(client_a.pk), "file": xlsx([HEAD])})
    assert r.status_code in (302, 403, 404)


def test_entry_page_has_excel_buttons(admin, client_a):
    html = http_for(admin).get(reverse("audits:new") + f"?client={client_a.pk}").content.decode()
    assert "Download Excel template" in html and "Upload Excel" in html
    assert reverse("audits:lines_upload") in html


# ---------------------------------------------------------------- signoff upload and preview


def test_detail_page_has_signoff_upload_for_staff_only(s3, setup, admin, user_a):  # noqa: F811
    audit_a, _, _ = setup
    url = reverse("audits:detail", args=[audit_a.pk])
    html = http_for(admin).get(url).content.decode()
    assert 'data-testid="signoff-upload"' in html and 'data-kind="signoff"' in html
    assert reverse("audits:presign", args=[audit_a.pk]) in html
    assert 'data-testid="signoff-upload"' not in http_for(user_a).get(url).content.decode()
    # the upload itself works from the audit page's endpoints
    pdf = upload(http_for(admin), audit_a, "signoff", "Signed.pdf", b"%PDF-1.4 signed", s3)
    assert pdf.kind == "signoff"
    assert reverse("audits:preview_file", args=[audit_a.pk, pdf.pk]) in http_for(user_a).get(url).content.decode()


def test_missing_file_preview_has_no_site_pages(s3, setup, admin, user_a):  # noqa: F811
    audit_a, _, _ = setup
    pdf = upload(http_for(admin), audit_a, "signoff", "Signed.pdf", b"%PDF-1.4 signed", s3)
    s3.delete_object(Bucket=BUCKET, Key=pdf.storage_key)
    assert storage.get_backend().exists(pdf.storage_key) is False
    r = http_for(user_a).get(reverse("audits:preview_file", args=[audit_a.pk, pdf.pk]))
    html = r.content.decode()
    assert r.status_code == 404 and "data-file-missing" in html
    assert "Go to home" not in html and "nav-link" not in html
    assert AuditFile.objects.filter(pk=pdf.pk).exists()


def test_framed_404_has_no_home_button(admin):
    h = http_for(admin)
    html = h.get("/no-such-page/", HTTP_SEC_FETCH_DEST="iframe").content.decode()
    assert "Page not found" in html and "Go to home" not in html
    assert "Go to home" in h.get("/no-such-page/").content.decode()


def test_local_backend_exists(tmp_path):
    b = storage.LocalBackend(tmp_path)
    b.put("a/b.pdf", b"x", "application/pdf")
    assert b.exists("a/b.pdf") and not b.exists("a/c.pdf") and not b.exists("../../etc/passwd")
