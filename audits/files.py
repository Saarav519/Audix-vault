"""File rules, photo processing (HEIC, GPS strip, thumbnails) and ZIP streaming."""

from __future__ import annotations

import io
import logging
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime

from audits.models import FileKind
from core import storage

logger = logging.getLogger(__name__)
MB = 1024 * 1024

CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
}


@dataclass(frozen=True)
class Rule:
    extensions: tuple
    max_bytes: int
    label: str


RULES = {
    FileKind.AUDIT_EXCEL: Rule((".xlsx", ".xls", ".csv"), 25 * MB, "Audit Excel"),
    FileKind.VARIANCE_REPORT: Rule((".xlsx", ".xls", ".csv"), 25 * MB, "Variance report"),
    FileKind.SCANNED_DATA: Rule((".pdf", ".jpg", ".jpeg", ".png"), 50 * MB, "Scanned data"),
    FileKind.SIGNOFF: Rule((".pdf", ".jpg", ".jpeg", ".png"), 25 * MB, "Signoff copy"),
    FileKind.PHOTO: Rule((".jpg", ".jpeg", ".png", ".webp", ".heic"), 15 * MB, "Photograph"),
}
MAX_PHOTOS = 30
REPORT_KINDS = (FileKind.AUDIT_EXCEL, FileKind.VARIANCE_REPORT, FileKind.SCANNED_DATA)


def heif_available() -> bool:
    try:
        import pillow_heif  # noqa: F401

        return True
    except Exception:
        return False


def validate_upload(kind: str, name: str, size: int) -> tuple[str | None, str | None]:
    """Returns (content_type, error)."""
    rule = RULES.get(kind)
    if rule is None:
        return None, "Unknown file kind."
    ext = storage.extension(name)
    if ext not in rule.extensions:
        return None, f"{rule.label}: allowed types are {', '.join(rule.extensions)}."
    if ext == ".heic" and not heif_available():
        return None, "HEIC photos are not supported on this server. Please upload JPEG."
    if size <= 0:
        return None, "The file is empty."
    if size > rule.max_bytes:
        return None, f"{rule.label}: the limit is {rule.max_bytes // MB} MB."
    return CONTENT_TYPES[ext], None


# ---------------------------------------------------------------- photos

GPS_TAG = 0x8825


def process_photo(backend, key: str, content_type: str, original_name: str):
    """Convert HEIC to JPEG, remove GPS location, make a 480px thumbnail.

    Returns (key, content_type, size, original_name, thumbnail_key).
    """
    from PIL import Image, ImageOps

    if content_type == "image/heic":
        import pillow_heif

        pillow_heif.register_heif_opener()
    data = backend.get(key)
    img = Image.open(io.BytesIO(data))
    exif = img.getexif()
    had_gps = GPS_TAG in exif
    if had_gps:
        del exif[GPS_TAG]
    if content_type == "image/heic" or had_gps:
        out = io.BytesIO()
        rgb = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        rgb.save(out, "JPEG", quality=92, exif=exif.tobytes())
        new_key = key.rsplit(".", 1)[0] + ".jpg"
        backend.put(new_key, out.getvalue(), "image/jpeg")
        if new_key != key:
            backend.delete(key)
        key, content_type = new_key, "image/jpeg"
        if original_name.lower().endswith(".heic"):
            original_name = original_name[:-5] + ".jpg"
        data = out.getvalue()
    thumb = ImageOps.exif_transpose(img).convert("RGB")
    if thumb.width > 480:
        thumb = thumb.resize((480, max(1, round(thumb.height * 480 / thumb.width))))
    tout = io.BytesIO()
    thumb.save(tout, "JPEG", quality=80)
    thumb_key = key.rsplit("/", 1)[0] + f"/thumbs/{uuid.uuid4().hex}.jpg"
    backend.put(thumb_key, tout.getvalue(), "image/jpeg")
    return key, content_type, len(data), original_name, thumb_key


# ---------------------------------------------------------------- zip


class _Sink:
    def __init__(self):
        self.buf = bytearray()

    def write(self, b):
        self.buf += b
        return len(b)

    def flush(self):
        pass

    def pop(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


def zip_entries(audit, files) -> list[tuple[str, str]]:
    ref = audit.reference
    folders = {FileKind.PHOTO: "Photographs", FileKind.SIGNOFF: "Signoff", FileKind.AUDIT_EXCEL: "Reports",
               FileKind.VARIANCE_REPORT: "Reports", FileKind.SCANNED_DATA: "Reports"}
    out, used = [], set()
    for i, f in enumerate(files, start=1):
        base = storage.safe_filename(f.original_name)
        if f.kind == FileKind.PHOTO:
            name = f"{ref}/{folders[f.kind]}/{i:02d}-{base}"
        elif f.kind in REPORT_KINDS:
            name = f"{ref}/{folders[f.kind]}/{f.get_kind_display()} - {base}"
        else:
            name = f"{ref}/{folders[f.kind]}/{base}"
        while name in used:
            name = name.replace(f"{ref}/", f"{ref}/{i}-", 1)
        used.add(name)
        out.append((name, f.storage_key))
    return out


def stream_zip(backend, entries):
    sink = _Sink()
    now = datetime.now().timetuple()[:6]
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for name, key in entries:
            info = zipfile.ZipInfo(name, date_time=now)
            info.compress_type = zipfile.ZIP_STORED
            try:
                with zf.open(info, mode="w", force_zip64=True) as dest:
                    for chunk in backend.stream(key):
                        dest.write(chunk)
                        if len(sink.buf) > 512 * 1024:
                            yield sink.pop()
            except Exception:
                logger.exception("ZIP: could not add %s", name)
            yield sink.pop()
    yield sink.pop()
