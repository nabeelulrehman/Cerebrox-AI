"""Fail-closed teacher resource scanning, run by a separate worker process."""
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import PendingObjectUpload, Resource
from .uploads import (
    MalwareDetected,
    clamav_scan_object,
    promote_uploaded_object,
    object_key_for_scan,
    quarantine_resource_object,
)

logger = logging.getLogger(__name__)


def reset_stale_scans():
    """Return abandoned leases to pending; resources remain inaccessible meanwhile."""
    stale_after = timedelta(seconds=max(600, settings_timeout_seconds() + 120))
    return Resource.objects.filter(
        scan_status=Resource.ScanStatus.SCANNING,
    ).filter(
        Q(scan_started_at__lt=timezone.now() - stale_after) | Q(scan_started_at__isnull=True),
    ).update(
        scan_status=Resource.ScanStatus.PENDING,
        scan_started_at=None,
        scan_error='Previous scan worker stopped before returning a verdict.',
    )


def settings_timeout_seconds():
    from django.conf import settings
    return settings.CLAMAV_TIMEOUT_SECONDS


def _record_failed(resource_id, error):
    with transaction.atomic():
        resource = Resource.objects.select_for_update().filter(pk=resource_id).first()
        if resource is None or resource.scan_status != Resource.ScanStatus.SCANNING:
            return
        resource.scan_status = Resource.ScanStatus.FAILED
        resource.scan_error = error
        resource.scan_started_at = None
        resource.save(update_fields=['scan_status', 'scan_error', 'scan_started_at'])


def scan_resource(resource_id, *, retry_failed=False):
    """Scan one resource. A missing, unavailable, or invalid verdict never passes it."""
    with transaction.atomic():
        resource = Resource.objects.select_for_update().filter(pk=resource_id).first()
        eligible = {Resource.ScanStatus.PENDING}
        if retry_failed:
            eligible.add(Resource.ScanStatus.FAILED)
        if resource is None or resource.scan_status not in eligible:
            return 'skipped'
        resource.scan_status = Resource.ScanStatus.SCANNING
        resource.scan_started_at = timezone.now()
        resource.scan_attempts += 1
        resource.scan_error = ''
        resource.save(update_fields=[
            'scan_status', 'scan_started_at', 'scan_attempts', 'scan_error',
        ])
        upload = PendingObjectUpload.objects.filter(resource=resource).first()
        source_key = object_key_for_scan(upload) if upload else resource.file.name
        expected_size = upload.size if upload else None

    if not source_key:
        _record_failed(resource_id, 'The private scan source is missing.')
        return 'failed'

    try:
        verdict = clamav_scan_object(source_key, expected_size=expected_size)
        if verdict is not True:
            raise RuntimeError('Scanner did not return a clean verdict.')
    except MalwareDetected:
        with transaction.atomic():
            resource = Resource.objects.select_for_update().filter(pk=resource_id).first()
            if resource is None or resource.scan_status != Resource.ScanStatus.SCANNING:
                return 'skipped'
            try:
                quarantined_key = quarantine_resource_object(source_key)
            except Exception as exc:
                # A failed quarantine copy never changes the result to clean.
                # The source remains private and is denied by scan_status.
                logger.error('Resource %s was infected; quarantine copy failed (%s).', resource_id, type(exc).__name__)
                quarantined_key = ''
            if quarantined_key:
                resource.file.name = quarantined_key
            resource.scan_status = Resource.ScanStatus.INFECTED
            resource.scan_error = 'ClamAV detected malware. The file is rejected.'
            resource.scan_started_at = None
            resource.scan_completed_at = timezone.now()
            resource.save(update_fields=[
                'file', 'scan_status', 'scan_error', 'scan_started_at', 'scan_completed_at',
            ])
        return 'infected'
    except Exception as exc:
        logger.warning('Resource %s scan failed (%s).', resource_id, type(exc).__name__)
        _record_failed(resource_id, 'Scanner unavailable or timed out. Retry is required.')
        return 'failed'

    try:
        with transaction.atomic():
            resource = Resource.objects.select_for_update().filter(pk=resource_id).first()
            if resource is None or resource.scan_status != Resource.ScanStatus.SCANNING:
                return 'skipped'
            upload = PendingObjectUpload.objects.filter(resource=resource).first()
            if upload is not None:
                final_key = promote_uploaded_object(upload)
                resource.file.name = final_key
            resource.scan_status = Resource.ScanStatus.CLEAN
            resource.scan_error = ''
            resource.scan_started_at = None
            resource.scan_completed_at = timezone.now()
            resource.save(update_fields=[
                'file', 'scan_status', 'scan_error', 'scan_started_at',
                'scan_completed_at',
            ])
            if upload is not None:
                from .uploads import delete_staged_upload
                transaction.on_commit(lambda: _delete_staged(upload, delete_staged_upload))
        return 'clean'
    except Exception as exc:
        logger.warning('Resource %s clean-file promotion failed (%s).', resource_id, type(exc).__name__)
        _record_failed(resource_id, 'Clean scan completed but promotion failed. Retry is required.')
        return 'failed'


def _delete_staged(upload, delete_function):
    try:
        delete_function(upload)
    except Exception as exc:
        # Keep the upload row so purge_pending_uploads can safely retry staging cleanup.
        logger.warning('Staging cleanup failed for upload %s (%s).', upload.pk, type(exc).__name__)


def scan_batch(*, batch_size=10, retry_failed=False):
    reset_stale_scans()
    statuses = [Resource.ScanStatus.PENDING]
    if retry_failed:
        statuses.append(Resource.ScanStatus.FAILED)
    resource_ids = list(
        Resource.objects.filter(scan_status__in=statuses)
        .order_by('created_at').values_list('pk', flat=True)[:batch_size]
    )
    results = {'clean': 0, 'infected': 0, 'failed': 0, 'skipped': 0}
    for resource_id in resource_ids:
        result = scan_resource(resource_id, retry_failed=retry_failed)
        results[result] += 1
    return results
