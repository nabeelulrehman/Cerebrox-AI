from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.utils import timezone

from apps.marketplace import services
from apps.marketplace.models import CommissionSettings, Payment, PaymentStatusAudit, Resource, TeachingSession
from apps.classrooms.models import Classroom
from apps.classrooms.services import is_active_member
from apps.teachers.models import TeacherPaymentMethod, TeacherProfile

User = get_user_model()
PW = 'StrongPass123'


def user(name, role):
    return User.objects.create_user(name, f'{name}@example.com', PW, role=role)


def approved_teacher(name):
    teacher = user(name, User.Role.TEACHER)
    TeacherProfile.objects.create(user=teacher, qualifications='MSc', subjects='Physics',
                                  verification_status=TeacherProfile.Status.APPROVED)
    return teacher


def proof():
    return SimpleUploadedFile('receipt.png', b'\x89PNG\r\n\x1a\nproof', content_type='image/png')


class TeacherManualPaymentTests(TestCase):
    def setUp(self):
        self.teacher = approved_teacher('teacher')
        self.other_teacher = approved_teacher('otherteacher')
        self.student = user('student', User.Role.STUDENT)
        self.method = TeacherPaymentMethod.objects.create(teacher=self.teacher,
            method=TeacherPaymentMethod.Method.JAZZCASH, account_title='Teacher', account_number='03001234567')
        self.resource = Resource.objects.create(teacher=self.teacher, title='Paid notes', file='private/test.pdf',
            visibility=Resource.Visibility.PAID, price=Decimal('1000.00'), scan_status=Resource.ScanStatus.CLEAN)
        self.client = Client()
        self.client.force_login(self.student)

    def test_submit_approve_snapshots_commission_and_grants_resource_access(self):
        CommissionSettings.objects.create(pk=1, percentage=Decimal('12.50'))
        response = self.client.post(f'/market/payments/teacher/resource/{self.resource.pk}/', {
            'payment_method': self.method.pk, 'transaction_reference': 'tx-001', 'receipt': proof(),
        })
        self.assertEqual(response.status_code, 302)
        payment = Payment.objects.get(student=self.student)
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertTrue(payment.is_manual_teacher_payment)
        self.assertEqual(payment.commission_percentage, Decimal('12.50'))
        self.assertEqual(payment.platform_commission, Decimal('125.00'))
        self.assertEqual(payment.teacher_earning, Decimal('875.00'))
        self.assertFalse(services.has_confirmed_payment(self.student, self.resource))

        decision = Client()
        decision.force_login(self.teacher)
        response = decision.post(f'/teachers/payments/{payment.pk}/decide/', {'decision': 'approve'})
        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CONFIRMED)
        self.assertTrue(services.has_confirmed_payment(self.student, self.resource))
        self.assertEqual(PaymentStatusAudit.objects.filter(payment=payment).count(), 2)

    def test_teacher_cannot_review_other_teachers_payment_and_rejection_requires_reason(self):
        payment = services.submit_teacher_payment(self.student, resource=self.resource, payment_method=self.method,
            transaction_reference='TX-002', receipt=proof())
        other = Client()
        other.force_login(self.other_teacher)
        response = other.post(f'/teachers/payments/{payment.pk}/decide/', {'decision': 'approve'})
        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        owner = Client()
        owner.force_login(self.teacher)
        owner.post(f'/teachers/payments/{payment.pk}/decide/', {'decision': 'reject'})
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        response = owner.post(f'/teachers/payments/{payment.pk}/decide/',
                              {'decision': 'reject', 'reason': 'Reference could not be matched.'})
        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.REJECTED)
        self.assertEqual(payment.review_note, 'Reference could not be matched.')

    def test_payment_method_details_and_receipt_are_owner_scoped(self):
        response = self.client.get(f'/market/payments/teacher/resource/{self.resource.pk}/')
        self.assertContains(response, '03001234567')
        payment = services.submit_teacher_payment(self.student, resource=self.resource, payment_method=self.method,
            transaction_reference='TX-003', receipt=proof())
        outsider = Client()
        outsider.force_login(user('outsider', User.Role.STUDENT))
        self.assertEqual(outsider.get(f'/market/payments/{payment.pk}/receipt/').status_code, 403)
        self.assertEqual(outsider.get(f'/market/payments/teacher/resource/{self.resource.pk}/').status_code, 200)

    def test_session_stays_pending_until_teacher_approval(self):
        session = TeachingSession.objects.create(teacher=self.teacher, title='Live class',
            starts_at=timezone.now() + timedelta(days=1), duration_minutes=60, price=Decimal('500.00'),
            capacity=2, access_type=TeachingSession.AccessType.PUBLIC_ONLY)
        payment = services.submit_teacher_payment(self.student, session=session, payment_method=self.method,
            transaction_reference='TX-004', receipt=proof())
        booking = session.bookings.get(student=self.student)
        self.assertEqual(booking.status, 'pending_payment')
        services.decide_teacher_payment(self.teacher, payment.pk, approve=True)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'confirmed')

    def test_teacher_registration_accepts_multiple_payment_methods(self):
        client = Client()
        response = client.post('/teachers/register/', {
            'username': 'newteacher', 'first_name': 'New', 'last_name': 'Teacher',
            'email': 'newteacher@example.com', 'password1': PW, 'password2': PW,
            'qualifications': 'MSc', 'subjects': 'Math', 'experience_years': '2',
            'teaching_mode': 'online', 'jazzcash_account': '03001112222',
            'easypaisa_account': '03003334444',
        })
        self.assertEqual(response.status_code, 302)
        new_teacher = User.objects.get(username='newteacher')
        self.assertEqual(new_teacher.teacher_payment_methods.count(), 2)

    def test_paid_class_join_requires_payment_then_teacher_approval(self):
        classroom = Classroom.objects.create(teacher=self.teacher, name='Paid class', subject='Physics',
            price=Decimal('750.00'), enrollment_policy=Classroom.Policy.APPROVAL)
        response = self.client.post('/classes/join/', {'code': classroom.join_code})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/market/payments/teacher/classroom/', response.url)
        self.assertFalse(is_active_member(self.student, classroom))
        response = self.client.post(response.url, {'payment_method': self.method.pk,
            'transaction_reference': 'CLASS-001', 'receipt': proof()})
        self.assertEqual(response.status_code, 302)
        payment = Payment.objects.get(classroom=classroom, student=self.student)
        self.assertFalse(is_active_member(self.student, classroom))
        services.decide_teacher_payment(self.teacher, payment.pk, approve=True)
        self.assertTrue(is_active_member(self.student, classroom))

    def test_only_admin_can_set_a_bounded_commission_rate(self):
        self.assertEqual(self.client.post('/admin-panel/payments/commission/',
            {'percentage': '12.50'}).status_code, 302)
        admin = user('admin', User.Role.ADMIN)
        admin_client = Client()
        admin_client.force_login(admin)
        admin_client.post('/admin-panel/payments/commission/', {'percentage': '150'})
        self.assertFalse(CommissionSettings.objects.filter(pk=1).exists())
        response = admin_client.post('/admin-panel/payments/commission/', {'percentage': '12.50'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CommissionSettings.objects.get(pk=1).percentage, Decimal('12.50'))
