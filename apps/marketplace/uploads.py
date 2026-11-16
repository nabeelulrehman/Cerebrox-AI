"""Validation and signing helpers for browser-to-object-storage uploads."""
from io import BytesIO
import os
import socket
import struct
import time
import zipfile
import warnings
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image

from .models import PendingObjectUpload, Resource
from .storage import object_storage_client


class MalwareDetected(Exception):
    """ClamAV returned a positive malware signature match."""


class ScannerUnavailable(Exception):
    """The configured scanner could not provide a trustworthy verdict."""


def clamav_scan_object(key, expected_size=None):
    """Stream a private S3 object to a private clamd daemon using INSTREAM."""
    host = settings.CLAMAV_HOST
    if not host:
        raise ScannerUnavailable('The ClamAV worker endpoint is not configured.')
    timeout = settings.CLAMAV_TIMEOUT_SECONDS
    client = object_storage_client()
    try:
        response = client.get_object(
            Bucket=settings.OBJECT_STORAGE_BUCKET,
            Key=Resource._meta.get_field('file').storage._normalize_name(key),
        )
        body = response['Body']
        with socket.create_connection((host, settings.CLAMAV_PORT), timeout=timeout) as scanner:
            deadline = time.monotonic() + timeout
            scanner.settimeout(timeout)
            scanner.sendall(b'zINSTREAM\x00')
            received = 0
            while True:
                block = body.read(64 * 1024)
                if time.monotonic() >= deadline:
                    raise ScannerUnavailable('ClamAV scan timed out.')
                if not block:
                    break
                received += len(block)
                if received > settings.MATERIAL_MAX_MB * 1024 * 1024:
                    raise ScannerUnavailable('The scanned object exceeded the configured size limit.')
                scanner.settimeout(max(0.1, deadline - time.monotonic()))
                scanner.sendall(struct.pack('!I', len(block)) + block)
            if expected_size is not None and received != expected_size:
                raise ScannerUnavailable('The scanned object size changed after upload verification.')
            scanner.sendall(struct.pack('!I', 0))
            verdict = bytearray()
            while b'\x00' not in verdict:
                scanner.settimeout(max(0.1, deadline - time.monotonic()))
                chunk = scanner.recv(4096)
                if not chunk:
                    break
                verdict.extend(chunk)
        result = bytes(verdict).split(b'\x00', 1)[0].decode('utf-8', errors='replace')
        if result.endswith(' OK'):
            return True
        if result.endswith(' FOUND'):
            raise MalwareDetected('ClamAV detected malware.')
        raise ScannerUnavailable('ClamAV returned an invalid or incomplete verdict.')
    except MalwareDetected:
        raise
    except ScannerUnavailable:
        raise
    except Exception as exc:
        raise ScannerUnavailable(f'ClamAV scan failed ({type(exc).__name__}).') from exc
    finally:
        try:
            body.close()
        except (UnboundLocalError, AttributeError):
            pass


def quarantine_resource_object(key):
    """Copy a rejected object to an inaccessible private quarantine prefix."""
    extension = os.path.splitext(key)[1].lower()
    quarantine_key = f'quarantine/resources/{uuid4().hex}{extension}'
    storage = Resource._meta.get_field('file').storage
    client = object_storage_client()
    client.copy_object(
        Bucket=settings.OBJECT_STORAGE_BUCKET,
        Key=storage._normalize_name(quarantine_key),
        CopySource={
            'Bucket': settings.OBJECT_STORAGE_BUCKET,
            'Key': storage._normalize_name(key),
        },
        MetadataDirective='REPLACE',
        Metadata={'scan-result': 'infected'},
        ContentType='application/octet-stream',
    )
    return quarantine_key


def storage_for_purpose(purpose):
    if purpose == PendingObjectUpload.Purpose.RESOURCE:
        return Resource._meta.get_field('file').storage
    if purpose == PendingObjectUpload.Purpose.AVATAR:
        return default_storage
    raise ValueError('Unsupported upload purpose.')


def object_key_for_upload(upload):
    storage = storage_for_purpose(upload.purpose)
    normalize = getattr(storage, '_normalize_name', lambda value: value)
    return normalize(upload.object_key)


def object_key_for_final(upload):
    storage = storage_for_purpose(upload.purpose)
    normalize = getattr(storage, '_normalize_name', lambda value: value)
    return normalize(upload.final_key)


def issue_upload_url(upload):
    storage = storage_for_purpose(upload.purpose)
    key = storage._normalize_name(upload.object_key)
    client = object_storage_client()
    url = client.generate_presigned_url(
        'put_object',
        Params={
            'Bucket': settings.OBJECT_STORAGE_BUCKET,
            'Key': key,
            'ContentLength': upload.size,
            'ContentType': upload.content_type,
            'Metadata': {
                'upload-id': str(upload.pk),
                'owner-id': str(upload.owner_id),
            },
        },
        ExpiresIn=600,
        HttpMethod='PUT',
    )
    return {
        'upload_id': str(upload.pk),
        'url': url,
        'method': 'PUT',
        'headers': {
            'Content-Type': upload.content_type,
            'x-amz-meta-upload-id': str(upload.pk),
            'x-amz-meta-owner-id': str(upload.owner_id),
        },
        'expires_in': 600,
    }


def verify_uploaded_object(upload):
    """Verify the completed write at the provider before attaching its key to a model."""
    if upload.expires_at <= timezone.now():
        raise ValueError('This upload authorization expired. Start the upload again.')
    client = object_storage_client()
    response = client.head_object(
        Bucket=settings.OBJECT_STORAGE_BUCKET,
        Key=object_key_for_upload(upload),
    )
    metadata = {key.lower(): value for key, value in response.get('Metadata', {}).items()}
    if metadata.get('upload-id') != str(upload.pk) or metadata.get('owner-id') != str(upload.owner_id):
        raise ValueError('The uploaded object does not match this authorization.')
    if response.get('ContentLength') != upload.size:
        raise ValueError('The uploaded file size does not match the selected file.')
    if response.get('ContentType') != upload.content_type:
        raise ValueError('The uploaded file type does not match the selected file.')
    return response


def snapshot_uploaded_object(upload):
    """Copy the browser-writable object to a server-only key before inspecting it."""
    extension = os.path.splitext(upload.original_name)[1].lower()
    prefix = 'upload-snapshots/resources' if upload.purpose == PendingObjectUpload.Purpose.RESOURCE else 'upload-snapshots/avatars'
    scan_key = upload.scan_key or f'{prefix}/{uuid4().hex}{extension}'
    if not upload.scan_key:
        # Persist before the provider call so cleanup can retry after partial failure.
        upload.scan_key = scan_key
        upload.save(update_fields=['scan_key'])
    client = object_storage_client()
    storage = storage_for_purpose(upload.purpose)
    client.copy_object(
        Bucket=settings.OBJECT_STORAGE_BUCKET,
        Key=storage._normalize_name(scan_key),
        CopySource={'Bucket': settings.OBJECT_STORAGE_BUCKET, 'Key': object_key_for_upload(upload)},
        MetadataDirective='REPLACE',
        Metadata={'upload-id': str(upload.pk), 'owner-id': str(upload.owner_id)},
        ContentType=upload.content_type,
    )
    head = client.head_object(
        Bucket=settings.OBJECT_STORAGE_BUCKET, Key=storage._normalize_name(scan_key),
    )
    metadata = {key.lower(): value for key, value in head.get('Metadata', {}).items()}
    if (head.get('ContentLength') != upload.size or head.get('ContentType') != upload.content_type
            or metadata.get('upload-id') != str(upload.pk)
            or metadata.get('owner-id') != str(upload.owner_id)):
        try:
            client.delete_object(Bucket=settings.OBJECT_STORAGE_BUCKET, Key=storage._normalize_name(scan_key))
        finally:
            raise ValueError('The immutable upload snapshot failed verification.')
    upload.scan_key = scan_key
    upload.save(update_fields=['scan_key'])
    return scan_key


def object_key_for_scan(upload):
    storage = storage_for_purpose(upload.purpose)
    normalize = getattr(storage, '_normalize_name', lambda value: value)
    return normalize(upload.scan_key or upload.object_key)


def verify_avatar_image(upload):
    verify_uploaded_object(upload)
    snapshot_uploaded_object(upload)
    response = object_storage_client().get_object(
        Bucket=settings.OBJECT_STORAGE_BUCKET,
        Key=object_key_for_scan(upload),
    )
    body = response['Body'].read(settings.AVATAR_UPLOAD_MAX_BYTES + 1)
    if len(body) > settings.AVATAR_UPLOAD_MAX_BYTES:
        raise ValueError('Profile images must be 5 MB or smaller.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            image = Image.open(BytesIO(body))
            if image.format not in {'JPEG', 'PNG', 'WEBP'}:
                raise ValueError('Upload a JPEG, PNG, or WebP image.')
            image.verify()
    except (Image.UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError) as exc:
        raise ValueError('The uploaded profile image is invalid.') from exc
    return True


class _S3RangeReader:
    """Seekable, bounded reader used by image and ZIP parsers without buffering 100 MB."""
    def __init__(self, client, key, size):
        self.client, self.key, self.size, self.position = client, key, size, 0

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=os.SEEK_SET):
        if whence == os.SEEK_SET:
            position = offset
        elif whence == os.SEEK_CUR:
            position = self.position + offset
        elif whence == os.SEEK_END:
            position = self.size + offset
        else:
            raise ValueError('Invalid seek mode.')
        if position < 0:
            raise ValueError('Invalid seek position.')
        self.position = min(position, self.size)
        return self.position

    def read(self, amount=-1):
        if self.position >= self.size:
            return b''
        end = self.size if amount is None or amount < 0 else min(self.position + amount, self.size)
        if end <= self.position:
            return b''
        response = self.client.get_object(
            Bucket=settings.OBJECT_STORAGE_BUCKET,
            Key=self.key,
            Range=f'bytes={self.position}-{end - 1}',
        )
        try:
            data = response['Body'].read()
        finally:
            response['Body'].close()
        self.position += len(data)
        return data


def validate_resource_content(upload):
    """Check real bytes and container structure before promotion to private resources."""
    extension = os.path.splitext(upload.original_name)[1].lower()
    expected_types = {
        '.pdf': 'application/pdf', '.ppt': 'application/vnd.ms-powerpoint',
        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        '.doc': 'application/msword',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.txt': 'text/plain', '.png': 'image/png', '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.mp4': 'video/mp4',
        '.webm': 'video/webm', '.zip': 'application/zip',
    }
    expected_type = expected_types.get(extension)
    if not expected_type or upload.content_type != expected_type:
        raise ValueError('The file extension and declared content type do not match.')
    client = object_storage_client()
    key = object_key_for_scan(upload)
    size = upload.size
    head_response = client.get_object(Bucket=settings.OBJECT_STORAGE_BUCKET, Key=key, Range='bytes=0-65535')
    try:
        head = head_response['Body'].read(65536)
    finally:
        head_response['Body'].close()
    if not head:
        raise ValueError('The uploaded file is empty or malformed.')

    if extension == '.pdf':
        tail_response = client.get_object(
            Bucket=settings.OBJECT_STORAGE_BUCKET, Key=key,
            Range=f'bytes={max(0, size - 2048)}-{size - 1}',
        )
        try:
            tail = tail_response['Body'].read()
        finally:
            tail_response['Body'].close()
        valid = head.startswith(b'%PDF-') and b'%%EOF' in tail.rstrip()
    elif extension in {'.png', '.jpg', '.jpeg', '.gif'}:
        from PIL import Image
        reader = _S3RangeReader(client, key, size)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                image = Image.open(reader)
                expected = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.gif': 'GIF'}[extension]
                if image.format != expected:
                    raise ValueError('The image content does not match its file extension.')
                image.verify()
            valid = True
        except (Image.UnidentifiedImageError, Image.DecompressionBombError,
                Image.DecompressionBombWarning, OSError, SyntaxError) as exc:
            raise ValueError('The uploaded image is malformed or unsafe.') from exc
    elif extension in {'.docx', '.pptx', '.zip'}:
        reader = _S3RangeReader(client, key, size)
        try:
            with zipfile.ZipFile(reader) as archive:
                entries = archive.infolist()
                names = {item.filename for item in entries}
                if len(entries) > 10000 or any(
                    name.startswith(('/', '\\')) or '..' in name.replace('\\', '/').split('/')
                    or item.flag_bits & 0x1
                    for item in entries for name in (item.filename,)
                ):
                    raise ValueError('The archive contains unsafe paths or entries.')
                if any(name.lower().endswith(('vbaproject.bin', '.exe', '.dll', '.js', '.html', '.hta')) for name in names):
                    raise ValueError('The archive contains an unsupported active file.')
                if extension == '.docx' and not {'[Content_Types].xml', 'word/document.xml'} <= names:
                    raise ValueError('The Word document container is malformed.')
                if extension == '.pptx' and not {'[Content_Types].xml', 'ppt/presentation.xml'} <= names:
                    raise ValueError('The PowerPoint container is malformed.')
            valid = True
        except (zipfile.BadZipFile, OSError, EOFError) as exc:
            raise ValueError('The uploaded archive or document is malformed.') from exc
    elif extension in {'.doc', '.ppt'}:
        valid = head.startswith(bytes.fromhex('D0CF11E0A1B11AE1'))
    elif extension == '.txt':
        # Text is downloaded as an attachment, never rendered as HTML.
        valid = not head.startswith((b'\x89PNG', b'%PDF-', b'PK\x03\x04', b'<!DOCTYPE', b'<html')) and b'\x00' not in head
        try:
            head.decode('utf-8-sig')
        except UnicodeDecodeError:
            valid = False
    elif extension == '.mp4':
        valid = len(head) >= 12 and head[4:8] == b'ftyp'
    else:  # WebM / Matroska EBML header.
        valid = head.startswith(bytes.fromhex('1A45DFA3'))
    if not valid:
        raise ValueError('The uploaded file content does not match its extension or is malformed.')
    return True


def promote_uploaded_object(upload):
    """Copy verified bytes to a fresh permanent key; caller retires staging after DB save."""
    client = object_storage_client()
    client.copy_object(
        Bucket=settings.OBJECT_STORAGE_BUCKET,
        Key=object_key_for_final(upload),
        CopySource={
            'Bucket': settings.OBJECT_STORAGE_BUCKET,
            'Key': object_key_for_scan(upload),
        },
        MetadataDirective='REPLACE',
        Metadata={'upload-id': str(upload.pk), 'owner-id': str(upload.owner_id)},
        ContentType=upload.content_type,
    )
    return upload.final_key


def delete_staged_upload(upload):
    """Remove browser staging and immutable scan keys after finalization."""
    client = object_storage_client()
    keys = [object_key_for_upload(upload)]
    if upload.scan_key:
        keys.append(object_key_for_scan(upload))
    for key in keys:
        client.delete_object(Bucket=settings.OBJECT_STORAGE_BUCKET, Key=key)


def delete_uploaded_object(upload):
    client = object_storage_client()
    keys = [object_key_for_upload(upload), object_key_for_final(upload)]
    if upload.scan_key:
        keys.append(object_key_for_scan(upload))
    for key in keys:
        client.delete_object(Bucket=settings.OBJECT_STORAGE_BUCKET, Key=key)
