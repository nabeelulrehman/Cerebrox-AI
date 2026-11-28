"""
Django settings for CerebroX AI.
"""
import os
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qsl
from dotenv import dotenv_values, load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / '.env'
if not ENV_FILE.is_file() and (BASE_DIR / 'env').is_file():
    ENV_FILE = BASE_DIR / 'env'
_DOTENV_VALUES = dotenv_values(ENV_FILE)
IS_VERCEL = os.environ.get('VERCEL', '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _environment_value(name, default=''):
    """Prefer an explicitly supplied environment value, but let .env fill blanks."""
    value = os.environ.get(name)
    if IS_VERCEL:
        return default if value is None else value
    if value is None or not value.strip():
        value = _DOTENV_VALUES.get(name)
    return default if value is None else value


def _normalize_gemini_api_key(value):
    value = (value or '').strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return ''.join(value.split())


_PROCESS_GEMINI_API_KEY = _normalize_gemini_api_key(os.environ.get('GEMINI_API_KEY'))
_DOTENV_GEMINI_API_KEY = _normalize_gemini_api_key(
    _DOTENV_VALUES.get('GEMINI_API_KEY')
)
if not IS_VERCEL:
    load_dotenv(dotenv_path=ENV_FILE)

def _env_bool(name, default=False):
    return _environment_value(name, str(default)).strip().lower() in {'1', 'true', 'yes', 'on'}


# Vercel's production mode must be determined from Vercel's process environment,
# never from a developer's local .env file.
DEBUG = (
    os.environ.get('DEBUG', 'False').strip().lower() in {'1', 'true', 'yes', 'on'}
    if IS_VERCEL else True
)
SECRET_KEY = _environment_value('SECRET_KEY', 'django-insecure-change-this-in-production')
configured_allowed_hosts = _environment_value('ALLOWED_HOSTS', '127.0.0.1,localhost')
ALLOWED_HOSTS = [host.strip() for host in _environment_value(
    'ALLOWED_HOSTS', '127.0.0.1,localhost'
).split(',') if host.strip()]
vercel_hostname = os.environ.get('VERCEL_URL', '').strip()
if vercel_hostname and vercel_hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(vercel_hostname)
if IS_VERCEL and not configured_allowed_hosts.strip():
    raise RuntimeError('Set exact production hostnames in ALLOWED_HOSTS for Vercel.')
if IS_VERCEL and any(host.startswith('.') or '*' in host for host in ALLOWED_HOSTS):
    raise RuntimeError('Use exact hostnames in ALLOWED_HOSTS on Vercel; wildcard hosts are not allowed.')
if IS_VERCEL and (len(SECRET_KEY) < 50 or SECRET_KEY.startswith('django-insecure-')):
    raise RuntimeError('Set a strong SECRET_KEY (at least 50 characters) in the Vercel project environment.')
if IS_VERCEL and DEBUG:
    raise RuntimeError('Set DEBUG=False in the Vercel project environment.')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # third-party
    'rest_framework',
    'corsheaders',

    # local apps
    'apps.accounts',
    'apps.learning',
    'apps.ai_engine',
    'apps.quizzes',
    'apps.analytics',
    'apps.dashboard',
    'apps.preparation',

    # OPTIONAL teacher ecosystem (independent learning never depends on these)
    'apps.teachers',
    'apps.classrooms',
    'apps.class_quizzes',
    'apps.marketplace',
    'apps.notifications',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # templates/ holds full pages, frontend/ holds reusable components & layouts
        'DIRS': [BASE_DIR / 'templates', BASE_DIR / 'frontend'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.notifications.context_processors.notification_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

if IS_VERCEL:
    # Only the Vercel project environment can select the production database.
    # Local .env values are deliberately ignored, even if they contain DATABASE_URL.
    DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
    if not DATABASE_URL:
        raise RuntimeError('Set DATABASE_URL to the existing Neon PostgreSQL database in Vercel.')
    parsed_database_url = urlparse(DATABASE_URL)
    if parsed_database_url.scheme not in {'postgres', 'postgresql', 'postgresql+psycopg'}:
        raise RuntimeError('DATABASE_URL must use PostgreSQL (postgresql://...).')
    database_options = dict(parse_qsl(parsed_database_url.query))
    database_options.setdefault('sslmode', 'require' if IS_VERCEL else 'prefer')
    database_options.setdefault('prepare_threshold', None)  # compatible with transaction-pooled serverless URLs
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': unquote(parsed_database_url.path.lstrip('/')),
        'USER': unquote(parsed_database_url.username or ''),
        'PASSWORD': unquote(parsed_database_url.password or ''),
        'HOST': parsed_database_url.hostname or '',
        'PORT': parsed_database_url.port or '',
        'CONN_MAX_AGE': 0,  # serverless invocations must not retain process-level DB connections
        'DISABLE_SERVER_SIDE_CURSORS': True,
        'OPTIONS': database_options,
    }}
else:
    # Local development always uses its own file and never opens a remote DB URL.
    DATABASE_URL = ''
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}

AUTH_USER_MODEL = 'accounts.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Karachi'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

OBJECT_STORAGE_BUCKET = _environment_value('OBJECT_STORAGE_BUCKET').strip()
OBJECT_STORAGE_ENDPOINT_URL = _environment_value('OBJECT_STORAGE_ENDPOINT_URL').strip().rstrip('/')
OBJECT_STORAGE_REGION = _environment_value('OBJECT_STORAGE_REGION', 'us-east-1').strip()
OBJECT_STORAGE_ACCESS_KEY = _environment_value('OBJECT_STORAGE_ACCESS_KEY').strip()
OBJECT_STORAGE_SECRET_KEY = _environment_value('OBJECT_STORAGE_SECRET_KEY').strip()
USE_OBJECT_STORAGE = bool(OBJECT_STORAGE_BUCKET)
if IS_VERCEL and not USE_OBJECT_STORAGE:
    raise RuntimeError('Set OBJECT_STORAGE_BUCKET and object storage credentials in Vercel; uploads cannot persist on Vercel.')
if IS_VERCEL and (not OBJECT_STORAGE_ACCESS_KEY or not OBJECT_STORAGE_SECRET_KEY):
    raise RuntimeError('Set OBJECT_STORAGE_ACCESS_KEY and OBJECT_STORAGE_SECRET_KEY in Vercel.')

STATICFILES_BACKEND = (
    'whitenoise.storage.CompressedManifestStaticFilesStorage' if not DEBUG
    else 'django.contrib.staticfiles.storage.StaticFilesStorage'
)

if USE_OBJECT_STORAGE:
    STORAGES = {
        'default': {'BACKEND': 'storages.backends.s3.S3Storage', 'OPTIONS': {'location': 'media'}},
        'staticfiles': {'BACKEND': STATICFILES_BACKEND},
    }
    AWS_STORAGE_BUCKET_NAME = OBJECT_STORAGE_BUCKET
    AWS_S3_ENDPOINT_URL = OBJECT_STORAGE_ENDPOINT_URL or None
    AWS_S3_REGION_NAME = OBJECT_STORAGE_REGION
    AWS_ACCESS_KEY_ID = OBJECT_STORAGE_ACCESS_KEY
    AWS_SECRET_ACCESS_KEY = OBJECT_STORAGE_SECRET_KEY
    AWS_S3_ADDRESSING_STYLE = 'path' if OBJECT_STORAGE_ENDPOINT_URL else 'virtual'
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    AWS_S3_FILE_OVERWRITE = False
else:
    STORAGES = {
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': STATICFILES_BACKEND},
    }

if not DEBUG:
    SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_HSTS_SECONDS = int(_environment_value('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', True)
    CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in _environment_value(
        'CSRF_TRUSTED_ORIGINS'
    ).split(',') if origin.strip()]
    if IS_VERCEL and vercel_hostname:
        generated_origin = f'https://{vercel_hostname}'
        if generated_origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(generated_origin)
    if IS_VERCEL and not CSRF_TRUSTED_ORIGINS:
        raise RuntimeError('Set exact HTTPS origins in CSRF_TRUSTED_ORIGINS for Vercel.')
    if IS_VERCEL and any('*' in origin for origin in CSRF_TRUSTED_ORIGINS):
        raise RuntimeError('Use exact HTTPS origins in CSRF_TRUSTED_ORIGINS on Vercel; wildcard origins are not allowed.')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'dashboard:home'
LOGOUT_REDIRECT_URL = 'public:landing'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

CORS_ALLOW_ALL_ORIGINS = DEBUG

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'loggers': {
        'apps.ai_engine': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# ---- AI configuration (apps/ai_engine) ----
GEMINI_API_KEY = _PROCESS_GEMINI_API_KEY or _DOTENV_GEMINI_API_KEY
GEMINI_API_KEY_SOURCE = (
    'process environment' if _PROCESS_GEMINI_API_KEY
    else 'environment file' if _DOTENV_GEMINI_API_KEY
    else 'not configured'
)
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite')
GEMINI_FALLBACK_MODEL = os.getenv('GEMINI_FALLBACK_MODEL', 'gemini-3.8-flash')
GEMINI_FALLBACK_MODEL_2 = os.getenv('GEMINI_FALLBACK_MODEL_2', '')
GEMINI_REQUEST_TIMEOUT_MS = int(os.getenv('GEMINI_REQUEST_TIMEOUT_MS', '150000'))
GEMINI_MAX_RETRIES = int(os.getenv('GEMINI_MAX_RETRIES', '2'))
GEMINI_RETRY_BASE_SECONDS = float(os.getenv('GEMINI_RETRY_BASE_SECONDS', '1'))

# ---- Teacher ecosystem (optional) ----
# Public base URL used when building shareable quiz links (falls back to the request host).
SITE_URL = os.getenv('SITE_URL', '').rstrip('/')

# Payment providers remain unavailable until both admin enablement and provider
# credentials/merchant approval are supplied. Secrets are never stored in Django.
PAYMENT_STRIPE_SECRET_KEY = _environment_value('PAYMENT_STRIPE_SECRET_KEY').strip()
PAYMENT_STRIPE_WEBHOOK_SECRET = _environment_value('PAYMENT_STRIPE_WEBHOOK_SECRET').strip()
PAYMENT_STRIPE_MODE = _environment_value('PAYMENT_STRIPE_MODE', 'test').strip().lower()
PAYMENT_JAZZCASH_MERCHANT_ID = _environment_value('PAYMENT_JAZZCASH_MERCHANT_ID').strip()
PAYMENT_JAZZCASH_PASSWORD = _environment_value('PAYMENT_JAZZCASH_PASSWORD').strip()
PAYMENT_JAZZCASH_INTEGRITY_SALT = _environment_value('PAYMENT_JAZZCASH_INTEGRITY_SALT').strip()
PAYMENT_JAZZCASH_MODE = _environment_value('PAYMENT_JAZZCASH_MODE', 'sandbox').strip().lower()
PAYMENT_EASYPAISA_STORE_ID = _environment_value('PAYMENT_EASYPAISA_STORE_ID').strip()
PAYMENT_EASYPAISA_HASH_KEY = _environment_value('PAYMENT_EASYPAISA_HASH_KEY').strip()
PAYMENT_EASYPAISA_MODE = _environment_value('PAYMENT_EASYPAISA_MODE', 'sandbox').strip().lower()
QUIZ_QR_TTL_SECONDS = int(os.getenv('QUIZ_QR_TTL_SECONDS', '120'))
QUIZ_SUBMIT_GRACE_SECONDS = int(os.getenv('QUIZ_SUBMIT_GRACE_SECONDS', '10'))

# Teacher materials are stored OUTSIDE MEDIA_ROOT and served only through a permission-checked view.
PRIVATE_MEDIA_ROOT = BASE_DIR / 'private_media'
MATERIAL_MAX_MB = int(os.getenv('MATERIAL_MAX_MB', '100'))
AVATAR_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024   # non-file request bodies
CLAMAV_HOST = _environment_value('CLAMAV_HOST').strip()
CLAMAV_PORT = int(_environment_value('CLAMAV_PORT', '3310'))
CLAMAV_TIMEOUT_SECONDS = int(_environment_value('CLAMAV_TIMEOUT_SECONDS', '360'))

# SMTP for emailing quiz links. With no EMAIL_HOST configured, mail is printed to the console
# (nothing is actually delivered) — set the EMAIL_* variables for real delivery.
EMAIL_HOST = _environment_value('EMAIL_HOST').strip()
if EMAIL_HOST:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_PORT = int(_environment_value('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = _environment_value('EMAIL_HOST_USER')
    EMAIL_HOST_PASSWORD = _environment_value('EMAIL_HOST_PASSWORD')
    EMAIL_USE_TLS = _env_bool('EMAIL_USE_TLS', True)
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = _environment_value('DEFAULT_FROM_EMAIL', 'CerebroX AI <no-reply@localhost>')

# LiveKit credentials are server-side only. Recording requires explicit opt-in and
# private S3-compatible storage; missing or placeholder values keep it unavailable.
LIVEKIT_URL = _environment_value('LIVEKIT_URL').strip()
LIVEKIT_API_KEY = _environment_value('LIVEKIT_API_KEY').strip()
LIVEKIT_API_SECRET = _environment_value('LIVEKIT_API_SECRET').strip()
LIVEKIT_RECORDING_ENABLED = _environment_value('LIVEKIT_RECORDING_ENABLED', 'False').strip().lower() == 'true'
LIVEKIT_RECORDING_VERIFIED = _environment_value('LIVEKIT_RECORDING_VERIFIED', 'False').strip().lower() == 'true'
LIVEKIT_RECORDING_S3_BUCKET = _environment_value('LIVEKIT_RECORDING_S3_BUCKET').strip()
LIVEKIT_RECORDING_S3_REGION = _environment_value('LIVEKIT_RECORDING_S3_REGION').strip()
LIVEKIT_RECORDING_S3_ACCESS_KEY = _environment_value('LIVEKIT_RECORDING_S3_ACCESS_KEY').strip()
LIVEKIT_RECORDING_S3_SECRET = _environment_value('LIVEKIT_RECORDING_S3_SECRET').strip()
LIVEKIT_RECORDING_S3_ENDPOINT = _environment_value('LIVEKIT_RECORDING_S3_ENDPOINT').strip().rstrip('/')

# ---- CerebroX business rules (apps/analytics) ----
WEAK_TOPIC_THRESHOLDS = {
    'STRONG': 70,
    'NEEDS_PRACTICE': 50,
}
XP_RULES = {
    'QUIZ_COMPLETED': 10,
    'SCORE_ABOVE_80': 20,
    'SCORE_ABOVE_90': 30,
}
