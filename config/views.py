from django.conf import settings
from django.http import FileResponse


def favicon(request):
    return FileResponse(
        (settings.BASE_DIR / 'static' / 'favicon.svg').open('rb'),
        content_type='image/svg+xml',
    )
