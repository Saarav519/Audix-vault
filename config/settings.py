"""Django settings for Audix Vault.

Everything configurable is read from environment variables (see .env.example).
"""

import logging
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
if (BASE_DIR / ".env").exists() and not os.environ.get("AUDIX_SKIP_DOTENV"):
    environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env.bool("DEBUG", default=False)

SECRET_KEY = env("SECRET_KEY", default="")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key-change-me"
    else:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")

# ---------------------------------------------------------------- hosts
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
RAILWAY_PUBLIC_DOMAIN = env("RAILWAY_PUBLIC_DOMAIN", default="")
if RAILWAY_PUBLIC_DOMAIN:
    ALLOWED_HOSTS.append(RAILWAY_PUBLIC_DOMAIN)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RAILWAY_PUBLIC_DOMAIN}")
if env("RAILWAY_ENVIRONMENT", default="") or RAILWAY_PUBLIC_DOMAIN:
    # Railway's health checker calls the service with this host header.
    ALLOWED_HOSTS.append("healthcheck.railway.app")
PORTAL_BASE_URL = env("PORTAL_BASE_URL", default="").rstrip("/")
if PORTAL_BASE_URL:
    parsed = urlparse(PORTAL_BASE_URL)
    if parsed.hostname:
        ALLOWED_HOSTS.append(parsed.hostname)
        CSRF_TRUSTED_ORIGINS.append(f"{parsed.scheme}://{parsed.netloc}")
elif RAILWAY_PUBLIC_DOMAIN:
    PORTAL_BASE_URL = f"https://{RAILWAY_PUBLIC_DOMAIN}"
if DEBUG:
    ALLOWED_HOSTS += ["localhost", "127.0.0.1", "[::1]", "testserver"]
ALLOWED_HOSTS = list(dict.fromkeys(ALLOWED_HOSTS))
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(CSRF_TRUSTED_ORIGINS))

# ---------------------------------------------------------------- apps
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "axes",
    "core",
    "accounts",
    "clients",
    "audits",
    "reports",
    "activity",
    "notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.middleware.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
    "accounts.middleware.AccountGuardMiddleware",
    "core.middleware.PortalContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.portal",
            ],
        },
    },
]

# ---------------------------------------------------------------- database
DATABASES = {
    "default": env.db(
        "DATABASE_URL", default="postgres://audix:audix@localhost:5432/audix"
    )
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- auth
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "accounts.backends.LoginIdBackend",
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "accounts:login"

# django-axes: 5 failures per login ID + IP locks for 15 minutes.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_USERNAME_FORM_FIELD = "username"
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "accounts/locked.html"
AXES_IPWARE_PROXY_COUNT = env.int("AXES_PROXY_COUNT", default=1)
AXES_IPWARE_META_PRECEDENCE_ORDER = ["HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"]
AXES_VERBOSE = False

# ---------------------------------------------------------------- sessions & security
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 30  # overridden per request from AppSettings
SESSION_SAVE_EVERY_REQUEST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "SAMEORIGIN"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    # The health check is called over plain HTTP from inside Railway.
    SECURE_REDIRECT_EXEMPT = [r"^healthz/?$"]
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 365)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
    SECURE_HSTS_PRELOAD = False

# ---------------------------------------------------------------- i18n
LANGUAGE_CODE = "en-in"
TIME_ZONE = env("TIME_ZONE", default="Asia/Kolkata")
USE_I18N = False
USE_TZ = True

# ---------------------------------------------------------------- static
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------- private file storage
S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", default="")
S3_BUCKET_NAME = env("S3_BUCKET_NAME", default="")
S3_ACCESS_KEY_ID = env("S3_ACCESS_KEY_ID", default="")
S3_SECRET_ACCESS_KEY = env("S3_SECRET_ACCESS_KEY", default="")
S3_REGION = env("S3_REGION", default="auto" if S3_ENDPOINT_URL else "ap-south-1")
S3_ADDRESSING_STYLE = env("S3_ADDRESSING_STYLE", default="virtual" if not S3_ENDPOINT_URL else "path")
SIGNED_URL_MINUTES = env.int("SIGNED_URL_MINUTES", default=10)
ALLOW_LOCAL_STORAGE = env.bool("ALLOW_LOCAL_STORAGE", default=False)
PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT", default=str(BASE_DIR / "private_media")))
MAX_ZIP_BYTES = env.int("MAX_ZIP_BYTES", default=500 * 1024 * 1024)
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

if S3_ENDPOINT_URL:
    _p = urlparse(S3_ENDPOINT_URL)
    STORAGE_ORIGINS = [f"{_p.scheme}://{_p.netloc}", f"{_p.scheme}://*.{_p.netloc}"]
elif S3_BUCKET_NAME:
    STORAGE_ORIGINS = ["https://*.amazonaws.com"]
else:
    STORAGE_ORIGINS = []

# Backups (see backup_database / sync_bucket_backup commands)
BACKUP_S3_ENDPOINT_URL = env("BACKUP_S3_ENDPOINT_URL", default="")
BACKUP_S3_BUCKET_NAME = env("BACKUP_S3_BUCKET_NAME", default="")
BACKUP_S3_ACCESS_KEY_ID = env("BACKUP_S3_ACCESS_KEY_ID", default="")
BACKUP_S3_SECRET_ACCESS_KEY = env("BACKUP_S3_SECRET_ACCESS_KEY", default="")
BACKUP_S3_REGION = env("BACKUP_S3_REGION", default=S3_REGION)

# ---------------------------------------------------------------- email
if env("EMAIL_URL", default=""):
    vars().update(env.email_url("EMAIL_URL"))
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Audix Vault <no-reply@audix.local>")
EMAIL_TIMEOUT = 15

# ---------------------------------------------------------------- demo / bootstrap
ALLOW_DEMO_SEED = env.bool("ALLOW_DEMO_SEED", default=False)

# ---------------------------------------------------------------- logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"stdout": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["stdout"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
        "axes": {"level": "WARNING"},
    },
}

# ---------------------------------------------------------------- sentry
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk

    def _scrub(event, hint):
        request = event.get("request") or {}
        for key in ("cookies", "data", "query_string"):
            request.pop(key, None)
        headers = request.get("headers") or {}
        for h in list(headers):
            if h.lower() in ("cookie", "authorization", "x-csrftoken"):
                headers[h] = "[scrubbed]"
        event.pop("user", None)
        return event

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        send_default_pii=False,
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
        before_send=_scrub,
    )

logging.captureWarnings(True)
