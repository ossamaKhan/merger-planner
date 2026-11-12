"""Settings for the MergeCo Franchise Planner.

Defaults to SQLite for local use. Set DATABASE_URL (e.g. your Supabase Postgres
connection string) to use Postgres instead.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Running on Vercel? (Vercel sets VERCEL=1.) Then: no debug, login required, Postgres only.
ON_VERCEL = bool(os.environ.get("VERCEL"))

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me-franchise-planner")
DEBUG = os.environ.get("DJANGO_DEBUG", "0" if ON_VERCEL else "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("DJANGO_CSRF_ORIGINS", "").split(",") if o]
if ON_VERCEL:
    CSRF_TRUSTED_ORIGINS += ["https://*.vercel.app"]
    for h in (os.environ.get("VERCEL_URL"), os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")):
        if h:
            CSRF_TRUSTED_ORIGINS.append(f"https://{h}")
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "planner",
]

MIDDLEWARE = [
    "django.middleware.gzip.GZipMiddleware",  # big KML / map JSON shrink ~10x (hosting caps responses at 4.5 MB)
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "planner.chunked.ChunkedUploadMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
try:  # whitenoise is optional for local runs
    import whitenoise  # noqa: F401
except ImportError:
    MIDDLEWARE.remove("whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
if os.environ.get("DATABASE_URL"):
    import dj_database_url  # pip install dj-database-url psycopg2-binary

    # Serverless (Vercel): no long-lived connections. Supabase pooler (port 6543) needs no server-side cursors.
    DATABASES["default"] = dj_database_url.parse(os.environ["DATABASE_URL"], conn_max_age=0 if ON_VERCEL else 600)
    if ":6543" in os.environ["DATABASE_URL"]:
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
elif ON_VERCEL:
    # Vercel's disk is temporary — without DATABASE_URL nothing would be kept. Fail loudly with a clear message.
    raise RuntimeError("Set DATABASE_URL (your Supabase Postgres connection string) in Vercel > Project > Settings > Environment Variables.")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "TIMEOUT": 60 * 30,
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Karachi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024

LOGIN_URL = "/admin/login/"
# Set PLANNER_REQUIRE_LOGIN=1 in production to gate every page behind Django auth.
PLANNER_REQUIRE_LOGIN = os.environ.get("PLANNER_REQUIRE_LOGIN", "1" if ON_VERCEL else "0") == "1"
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
# Name shown for "all regions together" (the whole circle)
PLANNER_CIRCLE_NAME = os.environ.get("PLANNER_CIRCLE_NAME", "Central B")
