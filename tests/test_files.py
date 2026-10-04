"""Stage 4: files and audit detail (acceptance 13, 15, 16, 19) using a fake S3 bucket (moto)."""

import io
import zipfile
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from django.test import Client as HttpClient
from django.test import override_settings
from django.urls import reverse
from moto import mock_aws
from PIL import Image

from activity.models import ActivityLog
from audits.models import AuditFile
from core import storage
from tests import factories as f

pytestmark = pytest.mark.django_db
BUCKET = "audix-test-bucket"


@pytest.fixture
def s3():
    with mock_aws(), override_settings(S3_BUCKET_NAME=BUCKET, S3_REGION="us-east-1", S3_ENDPOINT_URL="",
                                       S3_ACCESS_KEY_ID="test", S3_SECRET_ACCESS_KEY="test",
                                       S3_ADDRESSING_STYLE="virtual", S3_UPLOAD_MODE="direct"):
        storage._s3.cache_clear()
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield boto3.client("s3", region_name="us-east-1")
    storage._s3.cache_clear()


def jpeg_with_gps():
    img = Image.new("RGB", (1200, 900), (180, 220, 20))
    exif = Image.Exif()
    exif[0x0132] = "2026:10:04 10:00:00"  # DateTime
    exif[0x8825] = {1: "N", 2: (19.0, 4.0, 0.0)}
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


def upload(http, audit, kind, name, data, s3client=None):
    r = http.post(reverse("audits:presign", args=[audit.pk]),
                  {"kind": kind, "name": name, "size": len(data), "content_type": "x"})
    assert r.status_code == 200, r.content
    p = r.json()
    key = p["fields"]["key"] if p.get("fields", {}).get("key") else None
    if s3client is not None:
        s3client.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=p["content_type"])
    else:
        from django.core.files.uploadedfile import SimpleUploadedFile

        up = http.post(p["url"], {"file": SimpleUploadedFile(name, data, content_type=p["content_type"]),
                                  "Content-Type": p["content_type"]})
        assert up.status_code == 201, up.content
    r = http.post(reverse("audits:confirm", args=[audit.pk]), {"token": p["token"]})
    assert r.status_code == 200, r.content
    return AuditFile.objects.get(pk=r.json()["id"])


@pytest.fixture
def setup(client_a, client_b, user_a, user_b, admin):
    audit_a = f.make_audit(client_a)
    draft_a = f.make_audit(client_a, publish=False)
    audit_b = f.make_audit(client_b)
    return audit_a, draft_a, audit_b


def staff_http(admin):
    h = HttpClient()
    h.force_login(admin)
    return h


def test_s3_upload_flow_and_downloads(s3, setup, admin, user_a):
    audit_a, _, _ = setup
    staff = staff_http(admin)
    pdf = upload(staff, audit_a, "signoff", "Signoff copy.pdf", b"%PDF-1.4 fake", s3)
    xls = upload(staff, audit_a, "audit_excel", "Audit.xlsx", b"PK fake excel", s3)
    photo = upload(staff, audit_a, "photo", "IMG_1.jpg", jpeg_with_gps(), s3)
    assert photo.thumbnail_key and photo.content_type == "image/jpeg"
    stored = s3.get_object(Bucket=BUCKET, Key=photo.storage_key)["Body"].read()
    exif = Image.open(io.BytesIO(stored)).getexif()
    assert 0x8825 not in exif and exif.get(0x0132) == "2026:10:04 10:00:00"  # GPS gone, date kept
    assert pdf.storage_key.startswith(f"clients/{audit_a.client_id}/audits/{audit_a.pk}/signoff/")

    http = HttpClient()
    http.force_login(user_a)
    before = ActivityLog.objects.count()
    r = http.get(reverse("audits:download", args=[audit_a.pk, xls.pk]))
    assert r.status_code == 302
    q = parse_qs(urlparse(r["Location"]).query)
    assert q["X-Amz-Expires"] == ["600"]  # 10 minutes
    assert "attachment" in q["response-content-disposition"][0]
    assert ActivityLog.objects.count() == before + 1
    assert ActivityLog.objects.latest("created_at").action_type == "download"
    r = http.get(reverse("audits:preview_file", args=[audit_a.pk, pdf.pk]))
    assert r.status_code == 302 and "inline" in parse_qs(urlparse(r["Location"]).query)["response-content-disposition"][0]
    # 19: no preview for reports
    assert http.get(reverse("audits:preview_file", args=[audit_a.pk, xls.pk])).status_code == 404
    html = http.get(reverse("audits:detail", args=[audit_a.pk])).content.decode()
    assert reverse("audits:preview_file", args=[audit_a.pk, xls.pk]) not in html
    assert reverse("audits:preview_file", args=[audit_a.pk, pdf.pk]) in html
    assert reverse("audits:download", args=[audit_a.pk, xls.pk]) in html
    # thumbnail and zip
    assert http.get(reverse("audits:thumb", args=[audit_a.pk, photo.pk])).status_code == 200
    r = http.get(reverse("audits:zip", args=[audit_a.pk]) + "?what=all")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(b"".join(r.streaming_content)))
    names = z.namelist()
    assert len(names) == 3 and all(n.startswith(audit_a.reference + "/") for n in names)
    assert z.read([n for n in names if n.endswith(".pdf")][0]) == b"%PDF-1.4 fake"


def test_upload_rules(s3, setup, admin):
    audit_a, _, _ = setup
    staff = staff_http(admin)
    bad = staff.post(reverse("audits:presign", args=[audit_a.pk]), {"kind": "audit_excel", "name": "x.exe", "size": 10})
    assert bad.status_code == 400
    big = staff.post(reverse("audits:presign", args=[audit_a.pk]),
                     {"kind": "photo", "name": "x.jpg", "size": 16 * 1024 * 1024})
    assert big.status_code == 400
    ok = staff.post(reverse("audits:presign", args=[audit_a.pk]), {"kind": "signoff", "name": "s.pdf", "size": 10})
    assert ok.json()["content_type"] == "application/pdf"
    # confirm fails when nothing was uploaded
    r = staff.post(reverse("audits:confirm", args=[audit_a.pk]), {"token": ok.json()["token"]})
    assert r.status_code == 400


def test_local_storage_flow(setup, admin, user_a):
    audit_a, _, _ = setup
    staff = staff_http(admin)
    photo = upload(staff, audit_a, "photo", "p.png", _png(), None)
    http = HttpClient()
    http.force_login(user_a)
    r = http.get(reverse("audits:preview_file", args=[audit_a.pk, photo.pk]))
    assert r.status_code == 302
    served = http.get(r["Location"])
    assert served.status_code == 200 and "inline" in served["Content-Disposition"]
    # the signed link does not work without a session
    assert HttpClient().get(r["Location"]).status_code == 302


def _png():
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (0, 0, 0)).save(out, "PNG")
    return out.getvalue()


# ---------------------------------------------------------------- 13 and 16: isolation


def endpoints(audit, file_id):
    return [
        reverse("audits:detail", args=[audit.pk]),
        reverse("audits:download", args=[audit.pk, file_id]),
        reverse("audits:preview_file", args=[audit.pk, file_id]),
        reverse("audits:thumb", args=[audit.pk, file_id]),
        reverse("audits:zip", args=[audit.pk]) + "?what=all",
        reverse("audits:zip", args=[audit.pk]) + "?what=photos",
    ]


@pytest.mark.parametrize("idx", range(6))
def test_client_cannot_reach_other_clients_files(s3, setup, admin, user_b, idx):
    audit_a, _, _ = setup
    photo = upload(staff_http(admin), audit_a, "photo", "p.jpg", jpeg_with_gps(), s3)
    http = HttpClient()
    http.force_login(user_b)
    before = ActivityLog.objects.filter(action_type__in=["download", "view"]).count()
    assert http.get(endpoints(audit_a, photo.pk)[idx]).status_code == 404
    assert ActivityLog.objects.filter(action_type__in=["download", "view"]).count() == before


@pytest.mark.parametrize("idx", range(6))
def test_drafts_invisible_to_clients(s3, setup, admin, user_a, idx):
    _, draft_a, _ = setup
    photo = upload(staff_http(admin), draft_a, "photo", "p.jpg", jpeg_with_gps(), s3)
    http = HttpClient()
    http.force_login(user_a)
    assert http.get(endpoints(draft_a, photo.pk)[idx]).status_code == 404


def test_client_cannot_upload(setup, user_a):
    audit_a, _, _ = setup
    http = HttpClient()
    http.force_login(user_a)
    assert http.post(reverse("audits:presign", args=[audit_a.pk]), {"kind": "photo"}).status_code == 404
    assert http.post(reverse("audits:confirm", args=[audit_a.pk]), {"token": "x"}).status_code == 404


def test_store_manager_limited_to_stores(setup, client_a, password):
    from accounts.models import Role, User

    audit_a, _, _ = setup  # on store S01
    other = f.make_audit(client_a, store=client_a.stores.get(code="S02"))
    u = User.objects.create_user("sm", password, role=Role.CLIENT_STORE, client=client_a, must_change_password=False)
    u.allowed_stores.set([client_a.stores.get(code="S02")])
    http = HttpClient()
    http.force_login(u)
    assert http.get(reverse("audits:detail", args=[audit_a.pk])).status_code == 404
    assert http.get(reverse("audits:detail", args=[other.pk])).status_code == 200


def test_storage_not_configured_message(admin, client_a):
    with override_settings(ALLOW_LOCAL_STORAGE=False, S3_BUCKET_NAME=""):
        audit = f.make_audit(client_a, publish=False)
        r = staff_http(admin).get(reverse("audits:edit", args=[audit.pk]))
        assert "File storage is not configured yet" in r.content.decode()
        r = staff_http(admin).post(reverse("audits:presign", args=[audit.pk]), {"kind": "photo", "name": "a.jpg", "size": 5})
        assert r.status_code == 503


def test_detail_panel_and_view_logged(setup, user_a):
    audit_a, _, _ = setup
    http = HttpClient()
    http.force_login(user_a)
    r = http.get(reverse("audits:detail", args=[audit_a.pk]) + "?panel=1")
    assert r.status_code == 200 and "<html" not in r.content.decode()
    assert ActivityLog.objects.filter(action="Viewed audit", audit=audit_a).exists()


# ---------------------------------------------------------------- S3 proxy upload mode (default)


@pytest.fixture
def s3_proxy():
    with mock_aws(), override_settings(S3_BUCKET_NAME=BUCKET, S3_REGION="us-east-1", S3_ENDPOINT_URL="",
                                       S3_ACCESS_KEY_ID="test", S3_SECRET_ACCESS_KEY="test",
                                       S3_ADDRESSING_STYLE="virtual", S3_UPLOAD_MODE="proxy"):
        storage._s3.cache_clear()
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield boto3.client("s3", region_name="us-east-1")
    storage._s3.cache_clear()


def test_upload_mode_default_is_proxy():
    from django.conf import settings

    assert settings.S3_UPLOAD_MODE == "proxy"


def test_s3_proxy_presign_points_at_the_app(s3_proxy, setup, admin):
    audit_a, _, _ = setup
    r = staff_http(admin).post(reverse("audits:presign", args=[audit_a.pk]),
                               {"kind": "signoff", "name": "s.pdf", "size": 10})
    p = r.json()
    assert p["url"].startswith("/files/local/upload/") and p["fields"] == {}
    assert p["content_type"] == "application/pdf"


def test_s3_proxy_upload_flow(s3_proxy, setup, admin, user_a):
    audit_a, _, _ = setup
    staff = staff_http(admin)
    pdf = upload(staff, audit_a, "signoff", "Signoff.pdf", b"%PDF-1.4 proxied", None)
    photo = upload(staff, audit_a, "photo", "IMG.jpg", jpeg_with_gps(), None)
    obj = s3_proxy.get_object(Bucket=BUCKET, Key=pdf.storage_key)
    assert obj["Body"].read() == b"%PDF-1.4 proxied" and obj["ContentType"] == "application/pdf"
    stored = s3_proxy.get_object(Bucket=BUCKET, Key=photo.storage_key)["Body"].read()
    assert 0x8825 not in Image.open(io.BytesIO(stored)).getexif()
    assert s3_proxy.head_object(Bucket=BUCKET, Key=photo.thumbnail_key)["ContentLength"] > 0
    # downloads still go straight to the bucket with a 10-minute presigned link
    http = HttpClient()
    http.force_login(user_a)
    r = http.get(reverse("audits:download", args=[audit_a.pk, pdf.pk]))
    assert r.status_code == 302 and parse_qs(urlparse(r["Location"]).query)["X-Amz-Expires"] == ["600"]


def test_s3_proxy_upload_rejects_bad_requests(s3_proxy, setup, admin):
    from django.core.files.uploadedfile import SimpleUploadedFile

    audit_a, _, _ = setup
    staff = staff_http(admin)
    p = staff.post(reverse("audits:presign", args=[audit_a.pk]), {"kind": "signoff", "name": "s.pdf", "size": 10}).json()
    wrong_type = staff.post(p["url"], {"file": SimpleUploadedFile("s.pdf", b"x" * 10), "Content-Type": "image/png"})
    assert wrong_type.status_code == 400
    too_big = staff.post(p["url"], {"file": SimpleUploadedFile("s.pdf", b"x" * (26 * 1024 * 1024)),
                                    "Content-Type": "application/pdf"})
    assert too_big.status_code == 400
    assert staff.post("/files/local/upload/not-a-token/", {"file": SimpleUploadedFile("s.pdf", b"x")}).status_code == 403
    assert "Contents" not in s3_proxy.list_objects_v2(Bucket=BUCKET)


def test_proxy_endpoint_closed_in_direct_mode(s3, setup, admin):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core.storage import proxy_upload

    url = proxy_upload("clients/x/y.pdf", "application/pdf", 100)["url"]

    r = staff_http(admin).post(url, {"file": SimpleUploadedFile("y.pdf", b"x"), "Content-Type": "application/pdf"})
    assert r.status_code == 404
