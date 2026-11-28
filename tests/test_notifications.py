import re

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.classrooms.models import Classroom
from apps.marketplace.models import GuidanceMessage, GuidanceRequest, Payment, PaymentStatusAudit
from apps.marketplace.services import decide_teacher_payment
from apps.notifications.events import notify_guidance_message, notify_payment_submitted
from apps.notifications.models import Notification
from apps.teachers.models import TeacherProfile


@override_settings(SECURE_SSL_REDIRECT=False)
class NotificationTests(TestCase):
    def make_user(self, username, role):
        return User.objects.create_user(username, password='Testpass123!', role=role)

    def test_bell_is_in_each_module_header_and_there_is_no_notifications_page(self):
        module_pages = (
            (User.Role.STUDENT, 'dashboard:home'),
            (User.Role.TEACHER, 'teachers:dashboard'),
            (User.Role.ADMIN, 'admin_panel:dashboard'),
        )
        for role, page in module_pages:
            user = self.make_user(f'user-{role}', role)
            self.client.force_login(user)
            response = self.client.get(reverse(page))
            self.assertIn(response.status_code, (200, 409))
            self.assertIn('data-notification-trigger', response.content.decode())
        self.assertEqual(self.client.get('/notifications/').status_code, 404)

    def test_student_teacher_and_admin_dashboards_render_with_nullable_payment_relations(self):
        student = self.make_user('dashboard-student', User.Role.STUDENT)
        teacher = self.make_user('dashboard-teacher', User.Role.TEACHER)
        admin = self.make_user('dashboard-admin', User.Role.ADMIN)
        TeacherProfile.objects.create(
            user=teacher, qualifications='MSc', subjects='Physics',
            verification_status=TeacherProfile.Status.APPROVED,
        )
        classroom = Classroom.objects.create(teacher=teacher, name='Dashboard class')
        Payment.objects.create(student=student, teacher=teacher, classroom=classroom, amount=500)

        self.client.force_login(student)
        self.assertContains(self.client.get(reverse('dashboard:home')), 'Dashboard class')
        self.client.force_login(teacher)
        self.assertContains(self.client.get(reverse('teachers:dashboard')), 'Dashboard class')
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse('admin_panel:dashboard')).status_code, 200)

    def test_summary_and_marking_are_scoped_to_signed_in_user(self):
        first = self.make_user('first', User.Role.STUDENT)
        second = self.make_user('second', User.Role.STUDENT)
        own = Notification.objects.create(
            recipient=first, title='Payment confirmed', message='It is confirmed.',
            notification_type=Notification.Type.PAYMENT, target_url='/market/payments/history/',
        )
        other = Notification.objects.create(
            recipient=second, title='Private', message='Other user notice.',
            notification_type=Notification.Type.MESSAGE, target_url='/market/guidance/',
        )
        self.client.force_login(first)
        summary = self.client.get(reverse('notifications:summary')).json()
        self.assertEqual(summary['unread_count'], 1)
        self.assertEqual([item['id'] for item in summary['notifications']], [own.pk])
        self.assertEqual(self.client.post(reverse('notifications:read', args=[other.pk])).status_code, 404)
        self.client.post(reverse('notifications:read_all'))
        own.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(own.is_read)
        self.assertFalse(other.is_read)

    def test_mark_all_as_read_form_posts_with_csrf_and_clears_unread_notifications(self):
        user = self.make_user('mark-all', User.Role.STUDENT)
        notification = Notification.objects.create(
            recipient=user, title='Unread', message='Needs marking.',
            notification_type=Notification.Type.MESSAGE, target_url='/market/guidance/',
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.get(reverse('market:my_guidance'))
        token = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', response.content).group(1).decode()
        result = client.post(reverse('notifications:read_all'), {'csrfmiddlewaretoken': token})
        self.assertEqual(result.status_code, 200)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_opening_notification_marks_it_read_and_redirects_to_its_existing_target(self):
        student = self.make_user('notification-link', User.Role.STUDENT)
        notification = Notification.objects.create(
            recipient=student, title='New message', message='A reply arrived.',
            notification_type=Notification.Type.MESSAGE,
            target_url='/market/guidance/#conversation-29',
        )
        self.client.force_login(student)
        response = self.client.get(reverse('notifications:open', args=[notification.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], notification.target_url)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_message_and_payment_pages_show_their_relevant_notifications(self):
        student = self.make_user('student-pages', User.Role.STUDENT)
        admin = self.make_user('admin-pages', User.Role.ADMIN)
        Notification.objects.create(
            recipient=student, title='A message arrived', message='Teacher replied.',
            notification_type=Notification.Type.MESSAGE, target_url='/market/guidance/',
        )
        Notification.objects.create(
            recipient=student, title='Payment needs review', message='Your receipt is being reviewed.',
            notification_type=Notification.Type.PAYMENT, target_url='/market/payments/history/',
        )
        Notification.objects.create(
            recipient=admin, title='New payment submitted', message='Review the submitted payment.',
            notification_type=Notification.Type.PAYMENT, target_url='/admin-panel/payments/',
        )
        self.client.force_login(student)
        message_page = self.client.get(reverse('market:my_guidance'))
        self.assertContains(message_page, '1 unread message notification')
        self.assertNotContains(message_page, 'A message arrived')
        self.assertContains(self.client.get(reverse('market:payment_history')), 'Payment needs review')
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse('admin_panel:payment_reports')), 'New payment submitted')

    def test_new_guidance_message_notifies_other_party_with_conversation_target(self):
        student = self.make_user('student', User.Role.STUDENT)
        teacher = self.make_user('teacher', User.Role.TEACHER)
        conversation = GuidanceRequest.objects.create(
            student=student, teacher=teacher, message='Initial question', started_by=student,
        )
        message = GuidanceMessage.objects.create(conversation=conversation, sender=teacher, body='Reply')
        notification = notify_guidance_message(conversation, teacher, message)
        self.assertEqual(notification.recipient, student)
        self.assertEqual(notification.notification_type, Notification.Type.MESSAGE)
        self.assertIn(f'#conversation-{conversation.pk}', notification.target_url)

    def test_payment_review_events_notify_admin_teacher_and_student_without_duplicate_status(self):
        student = self.make_user('student', User.Role.STUDENT)
        teacher = self.make_user('teacher', User.Role.TEACHER)
        admin = self.make_user('admin', User.Role.ADMIN)
        classroom = Classroom.objects.create(teacher=teacher, name='Paid class', price=500)
        payment = Payment.objects.create(student=student, teacher=teacher, classroom=classroom, amount=500,
                                         is_manual_teacher_payment=True)
        submitted_audit = PaymentStatusAudit.objects.create(
            payment=payment, actor=student, old_status=Payment.Status.PENDING,
            new_status=Payment.Status.PENDING, note='Submitted',
        )
        notify_payment_submitted(payment, submitted_audit)
        self.assertTrue(Notification.objects.filter(recipient=admin, related_object_id=payment.pk).exists())
        self.assertTrue(Notification.objects.filter(recipient=teacher, related_object_id=payment.pk).exists())
        self.assertTrue(Notification.objects.filter(recipient=student, title='Payment awaiting review').exists())

        decide_teacher_payment(teacher, payment.pk, approve=True)
        self.assertEqual(Notification.objects.filter(recipient=student, related_object_id=payment.pk).count(), 2)
        teacher_notifications = Notification.objects.filter(recipient=teacher, related_object_id=payment.pk)
        self.assertEqual(teacher_notifications.count(), 2)
        self.assertTrue(teacher_notifications.filter(title='New payment submitted').exists())
        self.assertTrue(teacher_notifications.filter(title='New earning received').exists())
        self.assertEqual(Notification.objects.filter(recipient=admin, related_object_id=payment.pk).count(), 1)
