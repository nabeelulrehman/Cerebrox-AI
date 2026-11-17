from datetime import timedelta
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from apps.preparation.models import PreparationPlan, LearningSession, PreparationNotification
from apps.preparation.services import reschedule


class Command(BaseCommand):
    help = 'Create idempotent in-app preparation reminders and detect missed sessions.'

    def handle(self, *args, **options):
        now = timezone.now()
        created = 0
        for plan in PreparationPlan.objects.filter(active=True, deadline__gte=timezone.localdate()).select_related('student'):
            # Mark missed activities and notify once. Keep their original dates in session history.
            missed = plan.sessions.filter(status=LearningSession.Status.PENDING, scheduled_at__lt=now)
            missed_count = missed.count()
            for session in missed.select_related('topic'):
                session.history.append({'kind': 'missed', 'scheduled_at': session.scheduled_at.isoformat()})
                session.status = LearningSession.Status.MISSED
                session.save(update_fields=['status', 'history'])
                created += self._notify(plan, session, 'missed', f'missed:{session.pk}',
                    'Study session missed', f'You missed “{session.title}”. Review your plan and reschedule remaining activities.')
            if missed_count:
                moved, total = reschedule(plan)
                created += self._notify(plan, None, 'rescheduled', f'rescheduled:{plan.pk}:{timezone.localdate().isoformat()}',
                    'Study plan updated', f'{moved} of {total} unfinished activities were moved. {plan.reschedule_notice}')
            upcoming = plan.sessions.filter(status=LearningSession.Status.PENDING,
                scheduled_at__gte=now, scheduled_at__lte=now + timedelta(hours=24))
            for session in upcoming:
                created += self._notify(plan, session, 'upcoming', f'upcoming:{session.pk}',
                    'Study session coming up', f'“{session.title}” is scheduled within the next 24 hours.')
            for days in (7, 1):
                if plan.deadline == timezone.localdate() + timedelta(days=days):
                    created += self._notify(plan, None, 'deadline', f'deadline:{plan.pk}:{days}',
                        'Examination deadline approaching', f'{plan.exam_name} is {days} day(s) away. Check your remaining sessions and revision.')
        self.stdout.write(self.style.SUCCESS(f'Created {created} new preparation notifications.'))

    def _notify(self, plan, session, kind, key, title, message):
        item, was_created = PreparationNotification.objects.get_or_create(
            dedupe_key=key, defaults={'student': plan.student, 'plan': plan, 'session': session,
                                      'kind': kind, 'title': title, 'message': message})
        if was_created and plan.email_reminders and plan.student.email:
            send_mail(title, message, settings.DEFAULT_FROM_EMAIL, [plan.student.email], fail_silently=True)
        return int(was_created)
