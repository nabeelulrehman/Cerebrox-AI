"""
Django settings for CerebroX AI.
"""
import os
from pathlib import Path
from dotenv import dotenv_values, load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / '.env.example'
if not ENV_FILE.is_file() and (BASE_DIR / 'env').is_file():
    ENV_FILE = BASE_DIR / 'env'


def _normalize_gemini_api_key(value):
    value = (value or '').strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return ''.join(value.split())


_PROCESS_GEMINI_API_KEY = _normalize_gemini_api_key(os.environ.get('GEMINI_API_KEY'))
_DOTENV_GEMINI_API_KEY = _normalize_gemini_api_key(
    dotenv_values(ENV_FILE).get('GEMINI_API_KEY')
)
load_dotenv(dotenv_path=ENV_FILE)

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-change-this-in-production')
DEBUG = os.getenv('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')

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

    # OPTIONAL teacher ecosystem (independent learning never depends on these)
    'apps.teachers',
    'apps.classrooms',
    'apps.class_quizzes',
    'apps.marketplace',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

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

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

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
GEMINI_REQUEST_TIMEOUT_MS = int(os.getenv('GEMINI_REQUEST_TIMEOUT_MS', '150000'))
GEMINI_MAX_RETRIES = int(os.getenv('GEMINI_MAX_RETRIES', '2'))
GEMINI_RETRY_BASE_SECONDS = float(os.getenv('GEMINI_RETRY_BASE_SECONDS', '1'))

# ---- Teacher ecosystem (optional) ----
# Public base URL used when building shareable quiz links (falls back to the request host).
SITE_URL = os.getenv('SITE_URL', '').rstrip('/')
QUIZ_QR_TTL_SECONDS = int(os.getenv('QUIZ_QR_TTL_SECONDS', '120'))
QUIZ_SUBMIT_GRACE_SECONDS = int(os.getenv('QUIZ_SUBMIT_GRACE_SECONDS', '10'))

# Teacher materials are stored OUTSIDE MEDIA_ROOT and served only through a permission-checked view.
PRIVATE_MEDIA_ROOT = BASE_DIR / 'private_media'
MATERIAL_MAX_MB = int(os.getenv('MATERIAL_MAX_MB', '100'))
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024   # non-file request bodies

# SMTP for emailing quiz links. With no EMAIL_HOST configured, mail is printed to the console
# (nothing is actually delivered) — set the EMAIL_* variables for real delivery.
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
if EMAIL_HOST:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
    EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
    EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'CerebroX AI <no-reply@localhost>')

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
