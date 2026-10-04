"""Settings for the test suite: no network, fake S3, in-memory email."""

import os
import tempfile

os.environ["AUDIX_SKIP_DOTENV"] = "1"
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-for-production-use-0123456789")
os.environ["DEBUG"] = "0"
os.environ.setdefault("ALLOWED_HOSTS", "testserver,localhost")
os.environ.setdefault("SECURE_SSL_REDIRECT", "0")

from config.settings import *  # noqa: E402,F403

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
PRIVATE_MEDIA_ROOT = __import__("pathlib").Path(tempfile.mkdtemp(prefix="audix-test-media-"))
S3_ENDPOINT_URL = ""
S3_BUCKET_NAME = ""
ALLOW_LOCAL_STORAGE = True
ALLOW_DEMO_SEED = True
