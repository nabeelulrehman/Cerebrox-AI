import base64
import hashlib
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from livekit import api
from livekit.protocol.webhook import WebhookEvent

from apps.classrooms.models import Enrollment
from apps.marketplace import services
from apps.marketplace import uploads as object_uploads
from apps.marketplace import scanning as resource_scanning
from apps.marketplace.models import (
    GuidanceMessage, GuidanceRequest, Resource, Payment, TeachingSession, SessionBooking,
    SessionAttendance, Review, PendingObjectUpload, OrphanedAvatar,
)
from apps.teachers.models import TeacherProfile
from tests.test_teacher_ecosystem import make_student, make_teacher, make_class, enroll, login, User, PW


class MarketBase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.mkdtemp()
        cls.field = Resource._meta.get_field('file')
        cls.old = cls.field.storage
        cls.field.storage = FileSystemStorage(location=cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls.field.storage = cls.old
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.teacher = make_teacher()
        self.student = make_student()
        self.cl = make_class(self.teacher)

    def upload(self, visibility, **extra):
        data = {'title': 'Notes', 'description': 'd', 'visibility': visibility,
                'file': SimpleUploadedFile('lecture.pdf', b'%PDF-1.4 secret'), **extra}
        login(self.teacher).post('/market/teach/resources/', data)
        return Resource.objects.order_by('-id').first()


class MaterialAccessTests(MarketBase):
    def test_resources_page_uses_the_shared_student_sidebar_layout(self):
        response = login(self.student).get('/market/resources/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="sidebar"')
        self.assertContains(response, 'Shared Study Materials')
        self.assertContains(response, 'Study resources from verified teachers')
        self.assertContains(response, 'page-layout has-sidebar student-page-layout')

    def test_draft_class_public_and_paid_access_rules(self):
        draft = self.upload('draft')
        shared = self.upload('classes', classrooms=[self.cl.id])
        public = self.upload('public')
        paid = self.upload('paid', price='500')
        outsider, member = make_student('out'), make_student('mem')
        enroll(member, self.cl)
        def code(user, r):
            return login(user).get(f'/market/resources/{r.id}/download/').status_code
        self.assertEqual(Client().get(f'/market/resources/{public.id}/download/').status_code, 302)   # login required
        self.assertEqual([code(outsider, r) for r in (draft, shared, public, paid)], [404, 404, 200, 404])
        self.assertEqual([code(member, r) for r in (draft, shared, public, paid)], [404, 200, 200, 404])
        self.assertEqual(code(self.teacher, draft), 200)
        self.assertEqual(code(make_teacher('rival'), draft), 404)

    def test_download_is_attachment_and_file_not_under_media_url(self):
        pub = self.upload('public')
        r = login(self.student).get(f'/market/resources/{pub.id}/download/')
        self.assertIn('attachment', r['Content-Disposition'])
        self.assertEqual(r['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('lecture', pub.file.name)          # stored under a random name
        self.assertEqual(Client().get('/media/' + pub.file.name).status_code, 404)

    def test_dangerous_types_and_missing_price_rejected(self):
        login(self.teacher).post('/market/teach/resources/', {
            'title': 'x', 'visibility': 'public', 'file': SimpleUploadedFile('evil.html', b'<script>')})
        login(self.teacher).post('/market/teach/resources/', {
            'title': 'y', 'visibility': 'paid', 'file': SimpleUploadedFile('a.pdf', b'x')})
        login(self.teacher).post('/market/teach/resources/', {
            'title': 'z', 'visibility': 'classes', 'file': SimpleUploadedFile('a.pdf', b'x')})
        self.assertEqual(Resource.objects.count(), 0)

    def test_classes_option_cannot_target_another_teachers_class(self):
        other_cl = make_class(make_teacher('other'), name='Theirs')
        login(self.teacher).post('/market/teach/resources/', {
            'title': 'x', 'visibility': 'classes', 'classrooms': [other_cl.id],
            'file': SimpleUploadedFile('a.pdf', b'x')})
        self.assertEqual(Resource.objects.count(), 0)

    def test_students_cannot_upload(self):
        r = login(self.student).post('/market/teach/resources/', {
            'title': 'x', 'visibility': 'public', 'file': SimpleUploadedFile('a.pdf', b'x')})
        self.assertEqual(r.status_code, 403)


class DirectResourceUploadTests(MarketBase):
    def test_start_authorizes_teacher_and_signs_a_100_mb_direct_upload(self):
        with override_settings(USE_OBJECT_STORAGE=True, MATERIAL_MAX_MB=100), \
                patch('apps.marketplace.views.issue_upload_url', return_value={
                    'upload_id': 'signed-upload', 'url': 'https://storage.example/upload',
                    'method': 'PUT', 'headers': {}, 'expires_in': 600,
                }) as issue_url:
            response = login(self.teacher).post('/market/teach/resources/upload/start/', {
                'title': 'Class notes', 'description': 'Physics', 'visibility': 'public',
                'file_name': '../../lesson.pdf', 'file_size': str(100 * 1024 * 1024),
            })
            self.assertEqual(response.status_code, 200)
            upload = PendingObjectUpload.objects.get(owner=self.teacher)
            self.assertEqual(upload.purpose, PendingObjectUpload.Purpose.RESOURCE)
            self.assertEqual(upload.original_name, 'lesson.pdf')
            self.assertEqual(upload.size, 100 * 1024 * 1024)
            self.assertTrue(upload.object_key.startswith('upload-staging/resources/'))
            self.assertTrue(upload.final_key.startswith('teacher-resources/'))
            issue_url.assert_called_once_with(upload)

    def test_start_rejects_student_oversize_and_unsupported_files(self):
        with override_settings(USE_OBJECT_STORAGE=True, MATERIAL_MAX_MB=100):
            student_response = login(self.student).post('/market/teach/resources/upload/start/', {
                'file_name': 'lesson.pdf', 'file_size': '1',
            })
            self.assertEqual(student_response.status_code, 403)
            client = login(self.teacher)
            for filename, size in (
                ('lesson.pdf', 100 * 1024 * 1024 + 1),
                ('payload.html', 100),
                ('empty.pdf', 0),
            ):
                response = client.post('/market/teach/resources/upload/start/', {
                    'title': 'Class notes', 'visibility': 'public',
                    'file_name': filename, 'file_size': str(size),
                })
                self.assertEqual(response.status_code, 400)
            self.assertFalse(PendingObjectUpload.objects.exists())

    def test_finish_creates_a_private_pending_resource_for_background_scanning(self):
        upload = PendingObjectUpload.objects.create(
            owner=self.teacher,
            purpose=PendingObjectUpload.Purpose.RESOURCE,
            object_key='upload-staging/resources/random.pdf', final_key='teacher-resources/random.pdf',
            original_name='lesson.pdf', content_type='application/pdf', size=20,
            metadata={'title': 'Server title', 'description': 'd', 'visibility': 'public',
                      'price': '', 'classrooms': []},
        )
        with override_settings(USE_OBJECT_STORAGE=True), \
                patch('apps.marketplace.views.verify_uploaded_object') as verify, \
                patch('apps.marketplace.views.snapshot_uploaded_object', side_effect=lambda item: (setattr(item, 'scan_key', 'upload-snapshots/resources/snapshot.pdf'), item.save(update_fields=['scan_key']))) as snapshot, \
                patch('apps.marketplace.views.validate_resource_content') as validate_content:
            response = login(self.teacher).post('/market/teach/resources/upload/finish/', {
                'upload_id': str(upload.pk),
            })
        self.assertEqual(response.status_code, 202)
        resource = Resource.objects.get()
        self.assertEqual(resource.title, 'Server title')
        self.assertEqual(resource.file.name, 'upload-snapshots/resources/snapshot.pdf')
        self.assertEqual(resource.original_name, 'lesson.pdf')
        self.assertEqual(resource.scan_status, Resource.ScanStatus.PENDING)
        verify.assert_called_once()
        snapshot.assert_called_once()
        validate_content.assert_called_once()
        self.assertEqual(verify.call_args.args[0].object_key, upload.object_key)
        upload.refresh_from_db()
        self.assertEqual(upload.resource_id, resource.pk)
        self.assertEqual(upload.scan_key, resource.file.name)

    def test_pending_resource_is_hidden_from_students_and_downloads(self):
        resource = Resource.objects.create(
            teacher=self.teacher, title='Awaiting scan', file='upload-staging/resources/wait.pdf',
            original_name='wait.pdf', visibility=Resource.Visibility.PUBLIC,
            scan_status=Resource.ScanStatus.PENDING,
        )
        page = login(self.student).get('/market/resources/')
        self.assertNotContains(page, 'Awaiting scan')
        response = login(self.student).get(f'/market/resources/{resource.pk}/download/')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(services.can_access_resource(self.teacher, resource))

    def test_finish_cannot_use_another_teachers_upload(self):
        upload = PendingObjectUpload.objects.create(
            owner=make_teacher('other-upload-owner'),
            purpose=PendingObjectUpload.Purpose.RESOURCE,
            object_key='upload-staging/resources/other.pdf', final_key='teacher-resources/other.pdf',
            original_name='other.pdf',
            content_type='application/pdf', size=5,
            metadata={'title': 'Other', 'description': '', 'visibility': 'public', 'price': '', 'classrooms': []},
        )
        with override_settings(USE_OBJECT_STORAGE=True), \
                patch('apps.marketplace.views.verify_uploaded_object') as verify:
            response = login(self.teacher).post('/market/teach/resources/upload/finish/', {
                'upload_id': str(upload.pk),
            })
        self.assertEqual(response.status_code, 404)
        verify.assert_not_called()

    def test_invalid_object_cleanup_failure_keeps_retryable_upload_record(self):
        upload = PendingObjectUpload.objects.create(
            owner=self.teacher, purpose=PendingObjectUpload.Purpose.RESOURCE,
            object_key='upload-staging/resources/invalid.pdf', final_key='teacher-resources/invalid.pdf',
            original_name='invalid.pdf', content_type='application/pdf', size=10,
        )
        with override_settings(USE_OBJECT_STORAGE=True), \
                patch('apps.marketplace.views.verify_uploaded_object', side_effect=ValueError('malformed')), \
                patch('apps.marketplace.uploads.delete_uploaded_object', side_effect=OSError('provider down')):
            response = login(self.teacher).post('/market/teach/resources/upload/finish/', {
                'upload_id': str(upload.pk),
            })
        self.assertEqual(response.status_code, 400)
        self.assertTrue(PendingObjectUpload.objects.filter(pk=upload.pk).exists())

    @override_settings(USE_OBJECT_STORAGE=True, OBJECT_STORAGE_BUCKET='private-assets')
    def test_resource_content_validation_rejects_mime_mismatch_and_malformed_pdf(self):
        upload = PendingObjectUpload.objects.create(
            owner=self.teacher, purpose=PendingObjectUpload.Purpose.RESOURCE,
            object_key='upload-staging/resources/mismatch.pdf', final_key='teacher-resources/mismatch.pdf',
            original_name='lesson.pdf', content_type='text/plain', size=12,
        )
        with patch.object(object_uploads, 'object_storage_client') as client:
            with patch.object(object_uploads, 'storage_for_purpose') as storage_for:
                storage_for.return_value._normalize_name.return_value = upload.object_key
                with self.assertRaisesRegex(ValueError, 'extension and declared'):
                    object_uploads.validate_resource_content(upload)
                client.assert_not_called()

            upload.content_type = 'application/pdf'
            body = MagicMock()
            body.read.return_value = b'<html>bad</html>'
            client.return_value.get_object.return_value = {'Body': body}
            with patch.object(object_uploads, 'storage_for_purpose') as storage_for:
                storage_for.return_value._normalize_name.return_value = upload.object_key
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    object_uploads.validate_resource_content(upload)
            self.assertEqual(client.return_value.get_object.call_count, 2)

    @override_settings(USE_OBJECT_STORAGE=True, OBJECT_STORAGE_BUCKET='private-assets')
    def test_resource_content_validation_accepts_pdf_signature_and_trailer(self):
        upload = PendingObjectUpload.objects.create(
            owner=self.teacher, purpose=PendingObjectUpload.Purpose.RESOURCE,
            object_key='upload-staging/resources/valid.pdf', final_key='teacher-resources/valid.pdf',
            original_name='lesson.pdf', content_type='application/pdf', size=19,
        )
        first = MagicMock()
        first.read.return_value = b'%PDF-1.7\ncontent'
        tail = MagicMock()
        tail.read.return_value = b'content\n%%EOF'
        client = MagicMock()
        client.get_object.side_effect = [{'Body': first}, {'Body': tail}]
        with patch.object(object_uploads, 'object_storage_client', return_value=client), \
                patch.object(object_uploads, 'storage_for_purpose') as storage_for:
            storage_for.return_value._normalize_name.return_value = upload.object_key
            self.assertTrue(object_uploads.validate_resource_content(upload))
        self.assertEqual(client.get_object.call_count, 2)

    def test_object_storage_download_redirects_to_short_lived_private_url_after_access_check(self):
        resource = self.upload('public')
        storage = MagicMock()
        storage._normalize_name.return_value = 'private/teacher-resources/random.pdf'
        client = MagicMock()
        client.generate_presigned_url.return_value = 'https://storage.example/private-signed-get'
        with override_settings(USE_OBJECT_STORAGE=True), \
                patch.object(Resource._meta.get_field('file'), 'storage', storage), \
                patch('apps.marketplace.views.object_storage_client', return_value=client):
            response = login(self.student).get(f'/market/resources/{resource.pk}/download/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://storage.example/private-signed-get')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        call = client.generate_presigned_url.call_args
        self.assertEqual(call.args[0], 'get_object')
        self.assertEqual(call.kwargs['ExpiresIn'], 300)
        self.assertEqual(call.kwargs['Params']['Key'], 'private/teacher-resources/random.pdf')

    def test_object_storage_deletion_calls_private_storage_delete(self):
        resource = self.upload('draft')
        object_name = resource.file.name
        storage = MagicMock()
        with override_settings(USE_OBJECT_STORAGE=True), \
                patch.object(Resource._meta.get_field('file'), 'storage', storage):
            response = login(self.teacher).post(f'/market/teach/resources/{resource.pk}/delete/')
        self.assertEqual(response.status_code, 302)
        storage.delete.assert_called_once_with(object_name)
        self.assertFalse(Resource.objects.filter(pk=resource.pk).exists())


class ResourceScanningTests(MarketBase):
    def create_pending_resource(self, username='scan-owner'):
        owner = self.teacher if username == 'scan-owner' else make_teacher(username)
        upload = PendingObjectUpload.objects.create(
            owner=owner, purpose=PendingObjectUpload.Purpose.RESOURCE,
            object_key='upload-staging/resources/to-scan.pdf',
            scan_key='upload-snapshots/resources/to-scan.pdf',
            final_key='teacher-resources/scanned.pdf', original_name='lesson.pdf',
            content_type='application/pdf', size=123,
        )
        resource = Resource.objects.create(
            teacher=owner, title='Private until scanned', file=upload.scan_key,
            original_name=upload.original_name, visibility=Resource.Visibility.PUBLIC,
            scan_status=Resource.ScanStatus.PENDING,
        )
        upload.resource = resource
        upload.save(update_fields=['resource'])
        return resource, upload

    def test_clean_verdict_is_the_only_path_that_promotes_and_grants_access(self):
        resource, upload = self.create_pending_resource()
        with patch('apps.marketplace.scanning.clamav_scan_object', return_value=True) as scan, \
                patch('apps.marketplace.scanning.promote_uploaded_object', return_value=upload.final_key) as promote:
            result = resource_scanning.scan_resource(resource.pk)

        self.assertEqual(result, 'clean')
        resource.refresh_from_db()
        self.assertEqual(resource.scan_status, Resource.ScanStatus.CLEAN)
        self.assertEqual(resource.file.name, upload.final_key)
        scan.assert_called_once_with(upload.scan_key, expected_size=upload.size)
        promote.assert_called_once()
        self.assertTrue(services.can_access_resource(self.student, resource))

    def test_infected_verdict_quarantines_and_never_promotes(self):
        resource, upload = self.create_pending_resource()
        with patch('apps.marketplace.scanning.clamav_scan_object', side_effect=object_uploads.MalwareDetected()), \
                patch('apps.marketplace.scanning.quarantine_resource_object', return_value='quarantine/resources/bad.pdf'), \
                patch('apps.marketplace.scanning.promote_uploaded_object') as promote:
            result = resource_scanning.scan_resource(resource.pk)

        self.assertEqual(result, 'infected')
        resource.refresh_from_db()
        self.assertEqual(resource.scan_status, Resource.ScanStatus.INFECTED)
        self.assertEqual(resource.file.name, 'quarantine/resources/bad.pdf')
        self.assertFalse(services.can_access_resource(self.student, resource))
        promote.assert_not_called()

    def test_timeout_unavailable_or_invalid_scanner_verdict_fails_closed(self):
        resource, _ = self.create_pending_resource()
        with patch('apps.marketplace.scanning.clamav_scan_object', side_effect=object_uploads.ScannerUnavailable()):
            result = resource_scanning.scan_resource(resource.pk)
        self.assertEqual(result, 'failed')
        resource.refresh_from_db()
        self.assertEqual(resource.scan_status, Resource.ScanStatus.FAILED)
        self.assertFalse(services.can_access_resource(self.student, resource))

        resource.scan_status = Resource.ScanStatus.PENDING
        resource.save(update_fields=['scan_status'])
        with patch('apps.marketplace.scanning.clamav_scan_object', return_value=False):
            result = resource_scanning.scan_resource(resource.pk)
        self.assertEqual(result, 'failed')
        resource.refresh_from_db()
        self.assertEqual(resource.scan_status, Resource.ScanStatus.FAILED)

    def test_teacher_cannot_manage_another_teachers_resource(self):
        resource, _ = self.create_pending_resource('scan-other-owner')
        response = login(self.teacher).post(f'/market/teach/resources/{resource.pk}/delete/')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Resource.objects.filter(pk=resource.pk).exists())


class OrphanedAvatarCleanupTests(TestCase):
    def test_expired_pending_scan_is_retained_for_worker(self):
        owner = make_teacher('cleanup-pending-scan')
        upload = PendingObjectUpload.objects.create(
            owner=owner, purpose=PendingObjectUpload.Purpose.RESOURCE,
            object_key='upload-staging/resources/pending.pdf', final_key='teacher-resources/pending.pdf',
            original_name='pending.pdf', content_type='application/pdf', size=20,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        resource = Resource.objects.create(
            teacher=owner, title='Pending', file=upload.object_key,
            scan_status=Resource.ScanStatus.PENDING,
        )
        upload.resource = resource
        upload.save(update_fields=['resource'])
        with patch('apps.marketplace.management.commands.purge_pending_uploads.delete_uploaded_object') as delete_all, \
                patch('apps.marketplace.management.commands.purge_pending_uploads.delete_staged_upload') as delete_staging:
            call_command('purge_pending_uploads')
        delete_all.assert_not_called()
        delete_staging.assert_not_called()
        self.assertTrue(PendingObjectUpload.objects.filter(pk=upload.pk).exists())

    def test_clean_resource_cleanup_deletes_only_unreferenced_staging_key(self):
        owner = make_teacher('cleanup-clean-scan')
        upload = PendingObjectUpload.objects.create(
            owner=owner, purpose=PendingObjectUpload.Purpose.RESOURCE,
            object_key='upload-staging/resources/clean.pdf', final_key='teacher-resources/clean.pdf',
            original_name='clean.pdf', content_type='application/pdf', size=20,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        resource = Resource.objects.create(
            teacher=owner, title='Clean', file=upload.final_key,
            scan_status=Resource.ScanStatus.CLEAN,
        )
        upload.resource = resource
        upload.save(update_fields=['resource'])
        with patch('apps.marketplace.management.commands.purge_pending_uploads.delete_staged_upload') as delete_staging, \
                patch('apps.marketplace.management.commands.purge_pending_uploads.delete_uploaded_object') as delete_all:
            call_command('purge_pending_uploads')
        delete_staging.assert_called_once()
        delete_all.assert_not_called()
        resource.refresh_from_db()
        self.assertEqual(resource.file.name, upload.final_key)
        self.assertFalse(PendingObjectUpload.objects.filter(pk=upload.pk).exists())

    def test_cleanup_preserves_referenced_files_and_retries_failed_deletion(self):
        user = make_student('avatar-cleanup')
        orphan = OrphanedAvatar.objects.create(storage_key='profiles/old-avatar.jpg')
        user.avatar.name = orphan.storage_key
        user.save(update_fields=['avatar'])
        with patch('apps.marketplace.management.commands.purge_pending_uploads.default_storage.delete') as delete:
            call_command('purge_pending_uploads')
            delete.assert_not_called()
            self.assertFalse(OrphanedAvatar.objects.filter(pk=orphan.pk).exists())

            orphan = OrphanedAvatar.objects.create(storage_key='profiles/another-old-avatar.jpg')
            delete.side_effect = [OSError('temporary provider failure'), None]
            call_command('purge_pending_uploads')
            self.assertTrue(OrphanedAvatar.objects.filter(pk=orphan.pk).exists())
            call_command('purge_pending_uploads')
            self.assertFalse(OrphanedAvatar.objects.filter(pk=orphan.pk).exists())


class PresignedObjectStorageTests(TestCase):
    def setUp(self):
        self.owner = make_teacher('presign-owner')
        self.upload = PendingObjectUpload.objects.create(
            owner=self.owner,
            purpose=PendingObjectUpload.Purpose.RESOURCE,
            object_key='upload-staging/resources/file.pdf', final_key='teacher-resources/file.pdf',
            original_name='file.pdf',
            content_type='application/pdf', size=123,
            metadata={'title': 'File', 'visibility': 'public'},
        )

    @override_settings(USE_OBJECT_STORAGE=True, OBJECT_STORAGE_BUCKET='private-assets')
    def test_presign_binds_method_content_length_type_owner_and_upload_id(self):
        storage = MagicMock()
        storage._normalize_name.return_value = 'private/upload-staging/resources/file.pdf'
        client = MagicMock()
        client.generate_presigned_url.return_value = 'https://storage.example/signed'
        with patch.object(object_uploads, 'storage_for_purpose', return_value=storage), \
                patch.object(object_uploads, 'object_storage_client', return_value=client):
            result = object_uploads.issue_upload_url(self.upload)

        self.assertEqual(result['method'], 'PUT')
        self.assertEqual(result['url'], 'https://storage.example/signed')
        self.assertEqual(result['headers']['Content-Type'], 'application/pdf')
        self.assertEqual(result['headers']['x-amz-meta-owner-id'], str(self.owner.pk))
        call = client.generate_presigned_url.call_args
        self.assertEqual(call.args[0], 'put_object')
        params = call.kwargs
        self.assertEqual(params['Params']['ContentLength'], 123)
        self.assertEqual(params['Params']['Metadata']['upload-id'], str(self.upload.pk))
        self.assertEqual(params['ExpiresIn'], 600)

    @override_settings(USE_OBJECT_STORAGE=True, OBJECT_STORAGE_BUCKET='private-assets')
    def test_head_verification_rejects_wrong_size_and_wrong_owner_metadata(self):
        storage = MagicMock()
        storage._normalize_name.return_value = 'private/upload-staging/resources/file.pdf'
        client = MagicMock()
        client.head_object.return_value = {
            'Metadata': {'upload-id': str(self.upload.pk), 'owner-id': str(self.owner.pk)},
            'ContentLength': self.upload.size,
            'ContentType': self.upload.content_type,
        }
        with patch.object(object_uploads, 'storage_for_purpose', return_value=storage), \
                patch.object(object_uploads, 'object_storage_client', return_value=client):
            object_uploads.verify_uploaded_object(self.upload)
            client.head_object.return_value['ContentLength'] += 1
            with self.assertRaisesRegex(ValueError, 'size'):
                object_uploads.verify_uploaded_object(self.upload)
            client.head_object.return_value['ContentLength'] = self.upload.size
            client.head_object.return_value['Metadata']['owner-id'] = 'another-owner'
            with self.assertRaisesRegex(ValueError, 'authorization'):
                object_uploads.verify_uploaded_object(self.upload)

    @override_settings(USE_OBJECT_STORAGE=True, OBJECT_STORAGE_BUCKET='private-assets')
    def test_scan_snapshot_is_server_only_and_verified_before_it_is_used(self):
        storage = MagicMock()
        storage._normalize_name.side_effect = lambda name: f'private/{name}'
        client = MagicMock()
        client.head_object.return_value = {
            'Metadata': {'upload-id': str(self.upload.pk), 'owner-id': str(self.owner.pk)},
            'ContentLength': self.upload.size,
            'ContentType': self.upload.content_type,
        }
        with patch.object(object_uploads, 'storage_for_purpose', return_value=storage), \
                patch.object(object_uploads, 'object_storage_client', return_value=client):
            key = object_uploads.snapshot_uploaded_object(self.upload)

        self.assertEqual(key, self.upload.scan_key)
        self.assertTrue(key.startswith('upload-snapshots/resources/'))
        copy = client.copy_object.call_args.kwargs
        self.assertEqual(copy['CopySource']['Key'], 'private/upload-staging/resources/file.pdf')
        self.assertEqual(copy['Key'], f'private/{key}')
        client.head_object.assert_called_once_with(Bucket='private-assets', Key=f'private/{key}')
        self.assertEqual(object_uploads.object_key_for_scan(self.upload), key)

    @override_settings(USE_OBJECT_STORAGE=True, OBJECT_STORAGE_BUCKET='private-assets')
    def test_expired_upload_cannot_be_finalized(self):
        self.upload.expires_at = timezone.now() - timedelta(seconds=1)
        self.upload.save(update_fields=['expires_at'])
        with patch.object(object_uploads, 'object_storage_client') as client:
            with self.assertRaisesRegex(ValueError, 'expired'):
                object_uploads.verify_uploaded_object(self.upload)
        client.assert_not_called()

    @override_settings(USE_OBJECT_STORAGE=True, OBJECT_STORAGE_BUCKET='private-assets')
    def test_promote_copies_to_private_final_key_and_staging_cleanup_is_separate(self):
        self.upload.scan_key = 'upload-snapshots/resources/file.pdf'
        storage = MagicMock()
        storage._normalize_name.side_effect = lambda name: f'private/{name}'
        client = MagicMock()
        with patch.object(object_uploads, 'storage_for_purpose', return_value=storage), \
                patch.object(object_uploads, 'object_storage_client', return_value=client):
            final_key = object_uploads.promote_uploaded_object(self.upload)
            client.copy_object.assert_called_once_with(
                Bucket='private-assets',
                Key='private/teacher-resources/file.pdf',
                CopySource={
                    'Bucket': 'private-assets',
                    'Key': 'private/upload-snapshots/resources/file.pdf',
                },
                MetadataDirective='REPLACE',
                Metadata={'upload-id': str(self.upload.pk), 'owner-id': str(self.owner.pk)},
                ContentType='application/pdf',
            )
            client.delete_object.assert_not_called()
            self.assertEqual(final_key, self.upload.final_key)

            object_uploads.delete_staged_upload(self.upload)

        self.assertEqual(client.delete_object.call_count, 2)
        self.assertEqual(
            {call.kwargs['Key'] for call in client.delete_object.call_args_list},
            {'private/upload-staging/resources/file.pdf', 'private/upload-snapshots/resources/file.pdf'},
        )

    @override_settings(
        USE_OBJECT_STORAGE=True, OBJECT_STORAGE_BUCKET='private-assets',
        CLAMAV_HOST='clamav.private', CLAMAV_PORT=3310,
        CLAMAV_TIMEOUT_SECONDS=5, MATERIAL_MAX_MB=100,
    )
    def test_clamav_instream_protocol_requires_an_explicit_clean_verdict(self):
        storage = MagicMock()
        storage._normalize_name.return_value = 'private/upload-staging/resources/file.pdf'
        client = MagicMock()
        body = BytesIO(b'private file bytes')
        client.get_object.return_value = {'Body': body}
        scanner = MagicMock()
        scanner.__enter__.return_value = scanner
        scanner.recv.return_value = b'stream: OK\x00'
        with patch.object(object_uploads.Resource._meta.get_field('file'), 'storage', storage), \
                patch.object(object_uploads, 'object_storage_client', return_value=client), \
                patch.object(object_uploads.socket, 'create_connection', return_value=scanner) as connect:
            self.assertTrue(object_uploads.clamav_scan_object(self.upload.object_key, expected_size=18))
        connect.assert_called_once_with(('clamav.private', 3310), timeout=5)
        sent = [call.args[0] for call in scanner.sendall.call_args_list]
        self.assertEqual(sent[0], b'zINSTREAM\x00')
        self.assertIn(b'private file bytes', sent[1])
        self.assertEqual(sent[-1], b'\x00\x00\x00\x00')

    @override_settings(
        USE_OBJECT_STORAGE=True, OBJECT_STORAGE_BUCKET='private-assets',
        CLAMAV_HOST='clamav.private', CLAMAV_PORT=3310,
        CLAMAV_TIMEOUT_SECONDS=5, MATERIAL_MAX_MB=100,
    )
    def test_clamav_infected_invalid_and_size_mismatch_verdicts_never_pass(self):
        storage = MagicMock()
        storage._normalize_name.return_value = 'private/upload-staging/resources/file.pdf'
        client = MagicMock()
        client.get_object.return_value = {'Body': BytesIO(b'file bytes')}
        scanner = MagicMock()
        scanner.__enter__.return_value = scanner
        with patch.object(object_uploads.Resource._meta.get_field('file'), 'storage', storage), \
                patch.object(object_uploads, 'object_storage_client', return_value=client), \
                patch.object(object_uploads.socket, 'create_connection', return_value=scanner):
            scanner.recv.return_value = b'stream: Eicar-Test FOUND\x00'
            with self.assertRaises(object_uploads.MalwareDetected):
                object_uploads.clamav_scan_object(self.upload.object_key, expected_size=10)
            scanner.recv.return_value = b'stream: scanner error ERROR\x00'
            with self.assertRaises(object_uploads.ScannerUnavailable):
                object_uploads.clamav_scan_object(self.upload.object_key, expected_size=10)
            with self.assertRaises(object_uploads.ScannerUnavailable):
                object_uploads.clamav_scan_object(self.upload.object_key, expected_size=10)


class GuidanceConversationTests(MarketBase):
    def test_student_sends_and_teacher_replies_in_persisted_chronological_thread(self):
        student_client = login(self.student)
        page = student_client.get('/market/guidance/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'New Message')
        self.assertContains(page, 'data-guidance-compose-dialog')
        self.assertContains(page, 'data-guidance-compose-open')
        self.assertContains(page, 'data-guidance-compose-close')
        self.assertContains(page, 'data-recipient-search')
        self.assertContains(page, 'role="combobox"')
        page_html = page.content.decode()
        self.assertLess(page_html.index('guidance-page__header'), page_html.index('guidance-compose-launch'))
        self.assertLess(page_html.index('guidance-compose-launch'), page_html.index('guidance-conversations'))
        self.assertContains(page, self.teacher.username)

        search = student_client.get('/market/guidance/recipients/', {'q': self.teacher.username})
        self.assertEqual(search.status_code, 200)
        result_ids = [item['id'] for item in search.json()['results']]
        self.assertIn(self.teacher.pk, result_ids)
        self.assertNotIn(self.student.pk, result_ids)
        email_search = student_client.get('/market/guidance/recipients/', {'q': self.teacher.email})
        self.assertIn(self.teacher.pk, [item['id'] for item in email_search.json()['results']])

        response = student_client.post('/market/guidance/send/', {
            'recipient': self.teacher.pk,
            'subject': 'Help with physics',
            'message': 'I need help with motion.',
        })
        conversation = GuidanceRequest.objects.get()
        self.assertRedirects(
            response,
            f'/market/guidance/#conversation-{conversation.pk}',
            fetch_redirect_response=False,
        )
        self.assertEqual(conversation.started_by, self.student)
        self.assertEqual(conversation.message, 'I need help with motion.')

        teacher_client = login(self.teacher)
        inbox = teacher_client.get('/market/teach/inbox/')
        self.assertContains(inbox, 'Help with physics')
        self.assertContains(inbox, 'I need help with motion.')
        self.assertContains(inbox, 'type="text" maxlength="2000" required')
        self.assertContains(inbox, '>Send</button>')
        self.assertNotContains(inbox, 'guidance-reply__toggle')
        reply_response = teacher_client.post(
            f'/market/guidance/conversations/{conversation.pk}/reply/',
            {'message': 'Start by drawing a velocity diagram.'},
            follow=True,
        )
        self.assertEqual(reply_response.status_code, 200)
        self.assertContains(reply_response, 'Your reply was sent.')
        reply = GuidanceMessage.objects.get(conversation=conversation)
        self.assertEqual(reply.sender, self.teacher)
        self.assertEqual(reply.body, 'Start by drawing a velocity diagram.')
        self.assertEqual(conversation.__class__.objects.get(pk=conversation.pk).status, GuidanceRequest.Status.ANSWERED)

        thread = student_client.get('/market/guidance/').content.decode()
        self.assertLess(thread.index('I need help with motion.'), thread.index('Start by drawing a velocity diagram.'))
        self.assertIn(self.teacher.get_full_name() or self.teacher.username, thread)

    def test_teacher_can_start_a_thread_with_an_active_student_and_student_can_reply(self):
        enroll(self.student, self.cl)
        teacher_client = login(self.teacher)
        page = teacher_client.get('/market/guidance/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.student.username)
        search = teacher_client.get('/market/guidance/recipients/', {'q': self.student.username})
        self.assertEqual(search.status_code, 200)
        self.assertIn(self.student.pk, [item['id'] for item in search.json()['results']])

        response = teacher_client.post('/market/guidance/send/', {
            'recipient': self.student.pk,
            'subject': 'Class follow-up',
            'message': 'Here are the topics to review.',
        })
        conversation = GuidanceRequest.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(conversation.started_by, self.teacher)
        self.assertEqual(conversation.student, self.student)
        self.assertEqual(conversation.teacher, self.teacher)

        student_client = login(self.student)
        self.assertContains(student_client.get('/market/guidance/'), 'Here are the topics to review.')
        response = student_client.post(
            f'/market/guidance/conversations/{conversation.pk}/reply/',
            {'message': 'Thank you, I will review them.'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your reply was sent.')
        reply = GuidanceMessage.objects.get(conversation=conversation)
        self.assertEqual(reply.sender, self.student)
        self.assertEqual(conversation.__class__.objects.get(pk=conversation.pk).status, GuidanceRequest.Status.OPEN)

    def test_recipients_and_replies_are_limited_to_authorized_participants(self):
        conversation = GuidanceRequest.objects.create(
            student=self.student,
            teacher=self.teacher,
            subject='Private question',
            message='Private content',
            started_by=self.student,
        )
        outsider = make_student('outsider')
        outsider_client = login(outsider)
        self.assertEqual(
            outsider_client.post(
                f'/market/guidance/conversations/{conversation.pk}/reply/',
                {'message': 'Unauthorized reply'},
            ).status_code,
            404,
        )
        other_teacher = make_teacher('other-teacher')
        self.assertEqual(
            login(other_teacher).post(
                f'/market/guidance/conversations/{conversation.pk}/reply/',
                {'message': 'Unauthorized reply'},
            ).status_code,
            404,
        )
        invalid_recipient = login(self.teacher).post('/market/guidance/send/', {
            'recipient': outsider.pk,
            'subject': 'Not allowed',
            'message': 'This should not be sent.',
        })
        self.assertEqual(invalid_recipient.status_code, 400)
        self.assertContains(invalid_recipient, 'data-guidance-compose-invalid', status_code=400)
        self.assertEqual(GuidanceRequest.objects.count(), 1)
        self.assertFalse(GuidanceMessage.objects.exists())


class PaymentTests(MarketBase):
    def test_purchase_requires_admin_confirmation_before_access(self):
        paid = self.upload('paid', price='500')
        c = login(self.student)
        c.post(f'/market/resources/{paid.id}/buy/')
        p = Payment.objects.get()
        self.assertEqual(p.status, Payment.Status.PENDING)
        self.assertEqual(c.get(f'/market/resources/{paid.id}/download/').status_code, 404)    # claim alone grants nothing
        c.post(f'/market/resources/{paid.id}/buy/')                                           # duplicate claim
        self.assertEqual(Payment.objects.count(), 1)
        admin = User.objects.create_user('adm', 'a@x.com', PW, role=User.Role.ADMIN)
        # a student (or the teacher) cannot confirm their own payment
        for u in (self.student, self.teacher):
            login(u).post(f'/market/admin/payments/{p.id}/', {'decision': 'confirm'})
        p.refresh_from_db()
        self.assertEqual(p.status, Payment.Status.PENDING)
        login(admin).post(f'/market/admin/payments/{p.id}/', {'decision': 'confirm'})
        self.assertEqual(c.get(f'/market/resources/{paid.id}/download/').status_code, 200)
        self.assertEqual(login(make_student('other')).get(f'/market/resources/{paid.id}/download/').status_code, 404)

    def test_rejected_payment_grants_nothing_and_allows_new_claim(self):
        paid = self.upload('paid', price='100')
        p = services.request_purchase(self.student, paid)
        admin = User.objects.create_user('adm', 'a@x.com', PW, role=User.Role.ADMIN)
        services.decide_payment(admin, p.id, False)
        self.assertFalse(services.can_access_resource(self.student, paid))
        services.request_purchase(self.student, paid)
        with self.assertRaises(services.MarketError):
            services.decide_payment(admin, p.id, True)       # already decided


class SessionTests(MarketBase):
    def make_session(self, price=0, capacity=1):
        return TeachingSession.objects.create(teacher=self.teacher, title='Doubt clearing',
                                              starts_at=timezone.now() + timedelta(days=1), price=price,
                                              capacity=capacity)

    @override_settings(LIVEKIT_URL='', LIVEKIT_API_KEY='secret-value', LIVEKIT_API_SECRET='')
    def test_livekit_configuration_diagnostics_name_missing_variables_without_values(self):
        issues = services.livekit_configuration_issues()
        self.assertEqual(issues, ['LIVEKIT_URL is missing', 'LIVEKIT_API_SECRET is missing'])
        self.assertNotIn('secret-value', ' '.join(issues))

    def test_free_session_confirms_and_capacity_enforced(self):
        s = self.make_session()
        self.assertEqual(services.book_session(self.student, s).status, SessionBooking.Status.CONFIRMED)
        with self.assertRaises(services.MarketError):
            services.book_session(make_student('late'), s)
        with self.assertRaises(services.MarketError):
            services.book_session(self.student, s)

    def test_paid_session_confirmed_only_after_payment_confirmation(self):
        s = self.make_session(price=Decimal('800'))
        b = services.book_session(self.student, s)
        self.assertEqual(b.status, SessionBooking.Status.PENDING_PAYMENT)
        self.assertNotIn('meet.example', login(self.student).get('/market/sessions/').content.decode())
        self.assertNotIn('meet.example', login(self.student).get(f'/market/sessions/{s.pk}/').content.decode())
        admin = User.objects.create_user('adm', 'a@x.com', PW, role=User.Role.ADMIN)
        services.decide_payment(admin, Payment.objects.get().id, True)
        b.refresh_from_db()
        self.assertEqual(b.status, SessionBooking.Status.CONFIRMED)
        self.assertNotIn('meet.example', login(self.student).get(f'/market/sessions/{s.pk}/').content.decode())

    @override_settings(LIVEKIT_URL='', LIVEKIT_API_KEY='', LIVEKIT_API_SECRET='')
    def test_teacher_to_student_online_session_lifecycle_and_attendance(self):
        teacher_client = login(self.teacher)
        scheduled_date = timezone.localtime(timezone.now() + timedelta(days=2)).date()
        created = teacher_client.post('/market/teach/sessions/', {
            'title': 'Algebra class',
            'subject': 'Mathematics',
            'description': 'Linear equations',
            'scheduled_date': scheduled_date.isoformat(),
            'start_time': '10:00',
            'end_time': '11:30',
            'price': '0',
            'capacity': '2',
            'instructions': 'Bring your workbook.',
        })
        session = TeachingSession.objects.get(title='Algebra class')
        self.assertRedirects(created, f'/market/sessions/{session.pk}/')
        self.assertContains(teacher_client.get(f'/market/sessions/{session.pk}/'), 'Start Class')
        self.assertContains(teacher_client.get(f'/market/sessions/{session.pk}/'), 'Booked students')

        student_client = login(self.student)
        detail = student_client.get(f'/market/sessions/{session.pk}/')
        self.assertContains(detail, 'Mathematics')
        self.assertContains(detail, 'Bring your workbook.')
        self.assertContains(detail, 'Book this class')
        self.assertNotContains(detail, 'meet.google.com')
        self.assertContains(detail, 'LiveKit video classroom')
        booked = student_client.post(f'/market/sessions/{session.pk}/book/')
        self.assertRedirects(booked, f'/market/sessions/{session.pk}/')
        self.assertEqual(session.bookings.get().status, SessionBooking.Status.CONFIRMED)
        self.assertContains(student_client.get(f'/market/sessions/{session.pk}/'), 'Confirmed')

        teacher_client.post(f'/market/teach/sessions/{session.pk}/start/')
        session.refresh_from_db()
        self.assertEqual(session.status, TeachingSession.Status.ONGOING)
        self.assertContains(student_client.get(f'/market/sessions/{session.pk}/'), 'Join Class')
        join_response = student_client.get(f'/market/sessions/{session.pk}/join/')
        self.assertEqual(join_response.status_code, 302)
        self.assertEqual(join_response['Location'], f'/market/sessions/{session.pk}/classroom/')
        self.assertFalse(SessionAttendance.objects.filter(session=session).exists())
        self.assertContains(teacher_client.get(f'/market/sessions/{session.pk}/classroom/'),
                            'Class recording')
        self.assertContains(student_client.get(f'/market/sessions/{session.pk}/classroom/'),
                            'Live classes are unavailable')

        joined_at = timezone.now().replace(microsecond=0)
        joined = WebhookEvent(event='participant_joined', created_at=int(joined_at.timestamp()))
        joined.room.name = services.livekit_room_name(session)
        joined.participant.identity = services.livekit_participant_identity(session, self.student)
        services.process_livekit_webhook(joined)
        services.process_livekit_webhook(joined)
        left_at = joined_at + timedelta(seconds=60)
        left = WebhookEvent(event='participant_left', created_at=int(left_at.timestamp()))
        left.room.name = services.livekit_room_name(session)
        left.participant.identity = services.livekit_participant_identity(session, self.student)
        services.process_livekit_webhook(left)
        services.process_livekit_webhook(left)
        attendance = SessionAttendance.objects.get(session=session)
        self.assertEqual(attendance.join_count, 1)
        self.assertEqual(attendance.total_seconds, 60)
        self.assertIsNotNone(attendance.last_left_at)
        self.assertContains(teacher_client.get(f'/market/sessions/{session.pk}/'), self.student.username)

        unbooked = make_student('unbooked')
        unknown = WebhookEvent(event='participant_joined', created_at=int(joined_at.timestamp()))
        unknown.room.name = services.livekit_room_name(session)
        unknown.participant.identity = services.livekit_participant_identity(session, unbooked)
        services.process_livekit_webhook(unknown)
        self.assertFalse(SessionAttendance.objects.filter(session=session, student=unbooked).exists())

        session.recording_status = TeachingSession.RecordingStatus.RECORDING
        session.livekit_egress_id = 'egress-id'
        session.save(update_fields=['recording_status', 'livekit_egress_id'])
        with patch('apps.marketplace.services._stop_egress', new_callable=AsyncMock) as stop_egress:
            teacher_client.post(f'/market/teach/sessions/{session.pk}/end/')
        stop_egress.assert_awaited_once_with('egress-id')
        session.refresh_from_db()
        self.assertEqual(session.status, TeachingSession.Status.COMPLETED)
        self.assertEqual(session.recording_status, TeachingSession.RecordingStatus.STOPPING)
        self.assertNotContains(student_client.get(f'/market/sessions/{session.pk}/'), 'Join Class')

    @override_settings(
        LIVEKIT_URL='wss://unit-test.livekit.cloud',
        LIVEKIT_API_KEY='unit-test-key',
        LIVEKIT_API_SECRET='unit-test-secret',
    )
    def test_token_endpoint_authorizes_session_owner_and_confirmed_bookings(self):
        session = self.make_session()
        services.book_session(self.student, session)
        services.start_session(session)
        session.refresh_from_db()

        teacher_client = login(self.teacher)
        student_client = login(self.student)
        rival = make_student('unbooked')
        with patch('apps.marketplace.views.services.issue_livekit_token', return_value='signed-token') as issue_token:
            teacher_response = teacher_client.post(f'/market/sessions/{session.pk}/token/')
            self.assertEqual(teacher_response.status_code, 200)
            self.assertEqual(teacher_response.json(), {
                'url': 'wss://unit-test.livekit.cloud',
                'token': 'signed-token',
            })
            self.assertNotContains(teacher_response, 'unit-test-secret')
            self.assertTrue(issue_token.call_args.kwargs['is_teacher'])
            self.assertEqual(issue_token.call_args.args[1], self.teacher)

            student_response = student_client.post(f'/market/sessions/{session.pk}/token/')
            self.assertEqual(student_response.status_code, 200)
            self.assertFalse(issue_token.call_args.kwargs['is_teacher'])
            self.assertEqual(issue_token.call_args.args[1], self.student)

            self.assertEqual(login(rival).post(f'/market/sessions/{session.pk}/token/').status_code, 403)

    def test_signed_webhook_records_attendance_and_rejects_body_tampering(self):
        session = self.make_session()
        services.book_session(self.student, session)
        services.start_session(session)
        joined_at = timezone.now().replace(microsecond=0)
        payload = (
            '{"event":"participant_joined","room":{"name":"%s"},'
            '"participant":{"identity":"%s"},"createdAt":%d}'
        ) % (
            services.livekit_room_name(session),
            services.livekit_participant_identity(session, self.student),
            int(joined_at.timestamp()),
        )
        body = payload.encode()
        token = api.AccessToken('webhook-key', 'webhook-secret').with_identity('webhook').with_ttl(
            timedelta(minutes=2)
        ).with_sha256(base64.b64encode(hashlib.sha256(body).digest()).decode()).to_jwt()
        response = Client().post(
            '/market/livekit/webhook/',
            body,
            content_type='application/json',
            HTTP_AUTHORIZATION=token,
        )
        self.assertEqual(response.status_code, 401)

        with override_settings(
            LIVEKIT_URL='wss://webhook.livekit.cloud',
            LIVEKIT_API_KEY='webhook-key',
            LIVEKIT_API_SECRET='webhook-secret',
        ):
            token = api.AccessToken('webhook-key', 'webhook-secret').with_identity('webhook').with_ttl(
                timedelta(minutes=2)
            ).with_sha256(base64.b64encode(hashlib.sha256(body).digest()).decode()).to_jwt()
            response = Client().post(
                '/market/livekit/webhook/',
                body,
                content_type='application/json',
                HTTP_AUTHORIZATION=token,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            SessionAttendance.objects.get(session=session, student=self.student).join_count,
            1,
        )
        tampered = Client().post(
            '/market/livekit/webhook/',
            body + b' ',
            content_type='application/json',
            HTTP_AUTHORIZATION=token,
        )
        self.assertEqual(tampered.status_code, 401)

    def test_cancellation_releases_capacity_and_duplicate_bookings_are_blocked(self):
        session = self.make_session(capacity=1)
        student_client = login(self.student)
        student_client.post(f'/market/sessions/{session.pk}/book/')
        student_client.post(f'/market/sessions/{session.pk}/cancel/')
        booking = SessionBooking.objects.get(session=session, student=self.student)
        self.assertEqual(booking.status, SessionBooking.Status.CANCELLED)
        student_client.post(f'/market/sessions/{session.pk}/book/')
        booking.refresh_from_db()
        self.assertEqual(booking.status, SessionBooking.Status.CONFIRMED)
        with self.assertRaises(services.MarketError):
            services.book_session(self.student, session)

    @override_settings(
        EMAIL_HOST='smtp.example.test',
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    )
    def test_reschedule_and_cancellation_notify_booked_students(self):
        mail.outbox.clear()
        session = self.make_session()
        student_client = login(self.student)
        student_client.post(f'/market/sessions/{session.pk}/book/')
        self.assertIn(self.student.email, mail.outbox[0].to)

        new_date = timezone.localtime(timezone.now() + timedelta(days=3)).date()
        teacher_client = login(self.teacher)
        teacher_client.post(f'/market/teach/sessions/{session.pk}/edit/', {
            'title': session.title,
            'subject': 'Physics',
            'description': 'Updated schedule',
            'scheduled_date': new_date.isoformat(),
            'start_time': '10:00',
            'end_time': '11:00',
            'price': '0',
            'capacity': '1',
            'instructions': 'Join on time.',
        })
        session.refresh_from_db()
        self.assertEqual(timezone.localtime(session.starts_at).date(), new_date)
        self.assertTrue(any('rescheduled' in message.subject.lower() for message in mail.outbox))

        teacher_client.post(f'/market/teach/sessions/{session.pk}/cancel/')
        session.refresh_from_db()
        self.assertEqual(session.status, TeachingSession.Status.CANCELLED)
        self.assertEqual(session.bookings.get().status, SessionBooking.Status.CANCELLED)
        self.assertTrue(any('cancelled' in message.subject.lower() for message in mail.outbox))

    def test_unapproved_teacher_sessions_not_listed_or_bookable(self):
        pending = make_teacher('pend', approved=False)
        s = TeachingSession.objects.create(teacher=pending, title='Hidden', starts_at=timezone.now() + timedelta(days=1))
        self.assertNotIn('Hidden', login(self.student).get('/market/sessions/').content.decode())
        self.assertEqual(login(self.student).post(f'/market/sessions/{s.id}/book/').status_code, 404)


class ReviewAndGuidanceTests(MarketBase):
    def test_review_requires_relationship_moderation_and_escapes_html(self):
        pid = self.teacher.teacher_profile.id
        login(self.student).post(f'/market/reviews/{pid}/', {'rating': 5, 'comment': 'great'})
        self.assertEqual(Review.objects.count(), 0)                       # never taught by them
        enroll(self.student, self.cl)
        login(self.student).post(f'/market/reviews/{pid}/', {'rating': 5, 'comment': '<script>alert(1)</script>'})
        self.assertNotIn('alert(1)', Client().get(f'/teachers/directory/{pid}/').content.decode())   # pending: hidden
        admin = User.objects.create_user('adm', 'a@x.com', PW, role=User.Role.ADMIN)
        login(admin).post(f'/market/admin/reviews/{Review.objects.get().id}/', {'decision': 'approve'})
        html = Client().get(f'/teachers/directory/{pid}/').content.decode()
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>alert', html)
        self.assertEqual(login(self.student).post(f'/market/reviews/{pid}/', {'rating': 9}).status_code, 302)
        self.assertEqual(Review.objects.get().rating, 5)                  # invalid rating ignored

    def test_guidance_roundtrip_and_privacy(self):
        pid = self.teacher.teacher_profile.id
        login(self.student).post(f'/market/guidance/{pid}/', {'message': 'Help with limits'})
        g = self.teacher.guidance_inbox.get()
        rival = make_teacher('rival')
        login(rival).post(f'/market/teach/inbox/{g.id}/reply/', {'reply': 'hijack'})
        g.refresh_from_db()
        self.assertEqual(g.reply, '')
        login(self.teacher).post(f'/market/teach/inbox/{g.id}/reply/', {'reply': 'Sure!'})
        self.assertIn('Sure!', login(self.student).get('/market/guidance/').content.decode())
        pending = make_teacher('pend', approved=False)
        self.assertEqual(login(self.student).post(f'/market/guidance/{pending.teacher_profile.id}/',
                                                  {'message': 'hi'}).status_code, 404)
