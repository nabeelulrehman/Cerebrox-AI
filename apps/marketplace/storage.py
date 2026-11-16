"""Private teacher material storage, local for development and S3 in production."""
from django.conf import settings
from django.utils.deconstruct import deconstructible

if settings.USE_OBJECT_STORAGE:
    from storages.backends.s3 import S3Storage

    _StorageBase = S3Storage
else:
    from django.core.files.storage import FileSystemStorage

    _StorageBase = FileSystemStorage

@deconstructible
class PrivateMediaStorage(_StorageBase):
    """Keep material objects private; S3 URLs are signed and short lived."""
    def __init__(self, *args, **kwargs):
        if settings.USE_OBJECT_STORAGE:
            kwargs.setdefault('location', 'private')
        else:
            kwargs.setdefault('location', settings.PRIVATE_MEDIA_ROOT)
        super().__init__(*args, **kwargs)


def object_storage_client():
    """Build a SigV4 S3 client; SigV4 is required for short-lived presigned URLs."""
    if not settings.USE_OBJECT_STORAGE:
        raise RuntimeError('Persistent object storage is not configured.')

    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        endpoint_url=settings.OBJECT_STORAGE_ENDPOINT_URL or None,
        region_name=settings.OBJECT_STORAGE_REGION,
        aws_access_key_id=settings.OBJECT_STORAGE_ACCESS_KEY,
        aws_secret_access_key=settings.OBJECT_STORAGE_SECRET_KEY,
        config=Config(
            signature_version='s3v4',
            s3={'addressing_style': 'path' if settings.OBJECT_STORAGE_ENDPOINT_URL else 'virtual'},
        ),
    )
