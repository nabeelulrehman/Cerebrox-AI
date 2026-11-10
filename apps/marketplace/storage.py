"""Private local storage rooted at the configured private-media directory."""
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateMediaStorage(FileSystemStorage):
    """Resolve the path at runtime so migrations remain portable across hosts."""
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('location', settings.PRIVATE_MEDIA_ROOT)
        super().__init__(*args, **kwargs)
