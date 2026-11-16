from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from apps.marketplace.models import OrphanedAvatar, PendingObjectUpload, Resource
from apps.marketplace.uploads import delete_staged_upload, delete_uploaded_object


class Command(BaseCommand):
    help = 'Delete expired pending-upload objects and their authorization rows.'

    def handle(self, *args, **options):
        expired = PendingObjectUpload.objects.filter(expires_at__lte=timezone.now()).select_related('resource')
        deleted = 0
        failed = 0
        scanning = 0
        for upload in expired.iterator():
            try:
                with transaction.atomic():
                    upload = PendingObjectUpload.objects.select_for_update().select_related('resource').get(pk=upload.pk)
                    resource = upload.resource
                    if resource is not None and resource.scan_status in (
                        Resource.ScanStatus.PENDING,
                        Resource.ScanStatus.SCANNING,
                        Resource.ScanStatus.FAILED,
                    ):
                        scanning += 1
                        continue
                    source_keys = [upload.object_key]
                    if upload.scan_key:
                        source_keys.append(upload.scan_key)
                    references = Resource.objects.filter(file__in=source_keys)
                    if resource is not None:
                        references = references.exclude(pk=resource.pk)
                    source_is_referenced = references.exists()
                    if source_is_referenced:
                        failed += 1
                        self.stderr.write(f'Upload {upload.pk} still has a resource reference; retained for safety.')
                        continue
                    if resource is None:
                        delete_uploaded_object(upload)
                    else:
                        # A CLEAN resource references final_key; an INFECTED
                        # resource may still point at its staging source.
                        delete_staged_upload(upload)
                        if resource.scan_status == Resource.ScanStatus.INFECTED and resource.file.name in source_keys:
                            resource.file.name = ''
                            resource.save(update_fields=['file'])
                    upload.delete()
            except Exception:
                failed += 1
                self.stderr.write(f'Could not delete expired object for upload {upload.pk}.')
                continue
            deleted += 1
        avatars_deleted = 0
        avatars_failed = 0
        User = get_user_model()
        for orphan in OrphanedAvatar.objects.order_by('created_at').iterator():
            # Re-check immediately before deletion: admin edits or shared legacy
            # avatar paths may have made this key live again.
            if User.objects.filter(avatar=orphan.storage_key).exists():
                orphan.delete()
                continue
            try:
                default_storage.delete(orphan.storage_key)
            except Exception:
                avatars_failed += 1
                self.stderr.write(f'Could not delete orphaned avatar {orphan.pk}.')
                continue
            orphan.delete()
            avatars_deleted += 1
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {deleted} expired upload(s); {failed} object(s) need retry; '
            f'{scanning} scan job(s) retained. '
            f'Deleted {avatars_deleted} orphaned avatar(s); {avatars_failed} avatar(s) need retry.'
        ))
