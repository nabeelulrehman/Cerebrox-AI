import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, Client
from django.utils import timezone

from apps.classrooms.models import Enrollment
from apps.marketplace import services
from apps.marketplace.models import Resource, Payment, TeachingSession, SessionBooking, Review
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
                                              capacity=capacity, meeting_link='https://meet.example/abc')

    def test_free_session_confirms_and_capacity_enforced(self):
        s = self.make_session()
        self.assertEqual(services.book_session(self.student, s).status, SessionBooking.Status.CONFIRMED)
        with self.assertRaises(services.MarketError):
            services.book_session(make_student('late'), s)
        with self.assertRaises(services.MarketError):
            services.book_session(self.student, s)

    def test_paid_session_confirmed_only_after_payment_confirmation_and_link_hidden_until_then(self):
        s = self.make_session(price=Decimal('800'))
        b = services.book_session(self.student, s)
        self.assertEqual(b.status, SessionBooking.Status.PENDING_PAYMENT)
        self.assertNotIn('meet.example', login(self.student).get('/market/sessions/').content.decode())
        admin = User.objects.create_user('adm', 'a@x.com', PW, role=User.Role.ADMIN)
        services.decide_payment(admin, Payment.objects.get().id, True)
        b.refresh_from_db()
        self.assertEqual(b.status, SessionBooking.Status.CONFIRMED)
        self.assertIn('meet.example', login(self.student).get('/market/sessions/').content.decode())

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
