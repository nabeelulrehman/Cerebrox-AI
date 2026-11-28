from datetime import time, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import PreparationPlan, PreparationTopic, LearningSession
from .services import _allocate_schedule, _priority_value, _validate_allocated_schedule, generate_plan, reschedule


class PreparationPlanGenerationTests(TestCase):
    def setUp(self):
        self.student = get_user_model().objects.create_user(
            username='plan-student', password='test-password-123', role='student',
        )
        self.client.force_login(self.student)

    def response_for(self, label, index):
        name = f'Topic {index}'
        return {
            'topics': [{'name': name, 'description': 'Core syllabus topic', 'priority': label}],
            'sessions': [{
                'topic': name, 'kind': 'study', 'title': f'Study {name}',
                'objectives': 'Understand the topic', 'material': 'Read and practice', 'day_offset': 1,
            }],
        }

    @patch('apps.preparation.services.chat_json')
    def test_low_medium_high_priorities_save_and_show_plan(self, chat_json):
        labels = [('Low', 1), ('Medium', 3), ('High', 5)]
        chat_json.side_effect = [self.response_for(label, index) for index, (label, _) in enumerate(labels, 1)]

        for index, (label, expected_priority) in enumerate(labels, 1):
            with self.subTest(priority=label):
                response = self.client.post(reverse('preparation:create'), {
                    'exam_name': f'Exam {label}',
                    'exam_type': 'Final',
                    'deadline': (timezone.localdate() + timedelta(days=21)).isoformat(),
                    'syllabus_text': f'Topic {index}',
                    'learning_level': 'intermediate',
                    'goals': 'Pass with confidence',
                    'study_hours_per_week': 7,
                    'preferred_time': '18:00',
                    'session_minutes': 60,
                    'timezone': 'Asia/Karachi',
                })
                self.assertEqual(response.status_code, 302)
                plan = PreparationPlan.objects.get(exam_name=f'Exam {label}', student=self.student)
                self.assertEqual(plan.topics.get().priority, expected_priority)
                self.assertEqual(plan.sessions.count(), 1)
                self.assertEqual(plan.sessions.get().title, f'Study Topic {index}')
                activity = plan.sessions.get()
                self.assertEqual(activity.scheduled_end_at - activity.scheduled_at, timedelta(minutes=60))
                self.assertIsNotNone(plan.topics.get().learning_topic_id)

        dashboard = self.client.get(reverse('preparation:dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        for label, _ in labels:
            self.assertContains(dashboard, f'Exam {label}')

    def test_priority_mapping_accepts_labels_and_numeric_database_ranks(self):
        for value, expected in [('Low', 1), ('Medium', 3), ('High', 5), (1, 1), (3, 3), (5, 5), ('4', 4)]:
            with self.subTest(value=value):
                self.assertEqual(_priority_value(value), expected)

    def test_unknown_priority_is_rejected_before_topics_are_saved(self):
        chat_json_response = self.response_for('Urgent', 1)
        with patch('apps.preparation.services.chat_json', return_value=chat_json_response):
            response = self.client.post(reverse('preparation:create'), {
                'exam_name': 'Invalid priority exam',
                'exam_type': 'Final',
                'deadline': (timezone.localdate() + timedelta(days=21)).isoformat(),
                'syllabus_text': 'Topic 1',
                'learning_level': 'intermediate',
                'study_hours_per_week': 7,
                'preferred_time': '18:00',
                'session_minutes': 60,
                'timezone': 'Asia/Karachi',
            })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PreparationPlan.objects.filter(exam_name='Invalid priority exam').exists())
        self.assertEqual(PreparationTopic.objects.count(), 0)
        self.assertEqual(LearningSession.objects.count(), 0)

    def test_scheduler_places_distinct_slots_on_selected_days_in_plan_timezone(self):
        today = timezone.localdate()
        study_day = (today + timedelta(days=1)).weekday()
        plan = PreparationPlan.objects.create(
            student=self.student, exam_name='Scheduling test', deadline=today + timedelta(days=14),
            syllabus_text='Topic', study_days=[str(study_day)], preferred_time=time(18, 0),
            session_minutes=45, study_hours_per_week=6, timezone='Asia/Karachi',
        )
        slots = _allocate_schedule(plan, [45, 45, 45])
        self.assertEqual(len({start for start, _ in slots}), 3)
        for start, end in slots:
            self.assertEqual(start.weekday(), study_day)
            self.assertEqual(start.tzinfo.key, 'Asia/Karachi')
            self.assertEqual(end - start, timedelta(minutes=45))
        for (_, previous_end), (next_start, _) in zip(slots, slots[1:]):
            self.assertGreaterEqual(next_start, previous_end)

    def test_scheduler_enforces_flexible_weekly_capacity_and_rejects_overlaps(self):
        today = timezone.localdate()
        plan = PreparationPlan.objects.create(
            student=self.student, exam_name='Flexible schedule', deadline=today + timedelta(days=20),
            syllabus_text='Topic', study_days=[], study_hours_per_week=2,
            session_minutes=60, preferred_time=time(18, 0), timezone='Asia/Karachi',
        )
        slots = _allocate_schedule(plan, [60, 60, 60])
        by_week = {}
        for start, _ in slots:
            local_start = timezone.localtime(start, ZoneInfo(plan.timezone))
            year, week, _ = local_start.isocalendar()
            by_week[(year, week)] = by_week.get((year, week), 0) + 60
        self.assertTrue(all(minutes <= 120 for minutes in by_week.values()))
        self.assertGreaterEqual(len(by_week), 2)
        duplicate_slot = (slots[0][0], slots[0][0] + timedelta(minutes=30))
        with self.assertRaisesMessage(ValueError, 'duplicate or overlapping'):
            _validate_allocated_schedule(
                plan, [duplicate_slot, duplicate_slot], [30, 30],
                ['study', 'study'],
            )

    @patch('apps.preparation.services.chat_json')
    def test_generated_fourteen_activities_are_spread_staged_and_non_overlapping(self, chat_json):
        today = timezone.localdate()
        study_days = ['0', '2', '4']
        plan = PreparationPlan.objects.create(
            student=self.student, exam_name='Fourteen activity schedule',
            deadline=today + timedelta(days=35), syllabus_text='Seven topics',
            study_days=study_days, preferred_time=time(19, 15), study_hours_per_week=7,
            session_minutes=60, timezone='Asia/Karachi',
        )
        topics = [{'name': f'Topic {i}', 'description': 'Study topic', 'priority': 3} for i in range(1, 8)]
        sessions = []
        for index in range(1, 8):
            sessions.append({'topic': f'Topic {index}', 'kind': 'study', 'title': f'Study {index}', 'day_offset': index})
        for index in range(1, 4):
            sessions.append({'topic': f'Topic {index}', 'kind': 'quiz', 'title': f'Quiz {index}', 'day_offset': index})
        for index in range(1, 3):
            sessions.append({'topic': f'Topic {index}', 'kind': 'revision', 'title': f'Revision {index}', 'day_offset': index})
            sessions.append({'topic': f'Topic {index}', 'kind': 'mock', 'title': f'Mock {index}', 'day_offset': index})
        self.assertEqual(len(sessions), 14)
        chat_json.return_value = {'topics': topics, 'sessions': sessions}

        topic_count, activity_count = generate_plan(plan)
        activities = list(plan.sessions.select_related('topic').order_by('scheduled_at'))
        self.assertEqual((topic_count, activity_count, len(activities)), (7, 14, 14))
        starts = [activity.scheduled_at for activity in activities]
        self.assertEqual(len(set(starts)), 14)
        self.assertGreaterEqual((activities[-1].scheduled_at.date() - activities[0].scheduled_at.date()).days, 21)
        local_dates = {timezone.localtime(activity.scheduled_at, ZoneInfo(plan.timezone)).date() for activity in activities}
        self.assertGreaterEqual(len(local_dates), 5)
        weekly_minutes = {}
        for activity in activities:
            local_start = timezone.localtime(activity.scheduled_at, ZoneInfo(plan.timezone))
            local_end = timezone.localtime(activity.scheduled_end_at, ZoneInfo(plan.timezone))
            self.assertIn(str(local_start.weekday()), study_days)
            self.assertEqual(local_end - local_start, timedelta(minutes=activity.duration_minutes))
            self.assertEqual(activity.duration_minutes, plan.session_minutes)
            year, week, _ = local_start.isocalendar()
            weekly_minutes[(year, week)] = weekly_minutes.get((year, week), 0) + activity.duration_minutes
        self.assertTrue(all(minutes <= plan.study_hours_per_week * 60 for minutes in weekly_minutes.values()))
        for previous, current in zip(activities, activities[1:]):
            self.assertLessEqual(previous.scheduled_end_at, current.scheduled_at)
        for kind in ('study', 'quiz', 'revision', 'mock'):
            self.assertIn(kind, [activity.kind for activity in activities])
        first_topic_activities = {activity.kind: activity.scheduled_at for activity in activities if activity.topic.name == 'Topic 1'}
        self.assertLess(first_topic_activities['study'], first_topic_activities['quiz'])
        self.assertLess(first_topic_activities['quiz'], first_topic_activities['revision'])
        self.assertLess(first_topic_activities['revision'], first_topic_activities['mock'])
        detail = self.client.get(reverse('preparation:detail', args=[plan.pk]))
        self.assertEqual(detail.status_code, 200)
        current = detail.context['current_session']
        self.assertIsNotNone(current)
        self.assertContains(detail, current.start_display)
        self.assertContains(detail, current.title)

    def test_detail_repairs_legacy_collapsed_dates_and_displays_saved_dates(self):
        today = timezone.localdate()
        plan = PreparationPlan.objects.create(
            student=self.student, exam_name='Collapsed legacy schedule',
            deadline=today + timedelta(days=28), syllabus_text='Topic',
            study_days=[], preferred_time=time(19, 15), study_hours_per_week=7,
            session_minutes=60, timezone='UTC',
        )
        repeated_start = timezone.localtime(timezone.now() + timedelta(days=7), ZoneInfo(plan.timezone)).replace(
            hour=19, minute=15, second=0, microsecond=0,
        )
        for index in range(7):
            LearningSession.objects.create(
                plan=plan, title=f'Legacy activity {index}', kind='study',
                scheduled_at=repeated_start, scheduled_end_at=repeated_start + timedelta(minutes=60),
                duration_minutes=60,
            )
        response = self.client.get(reverse('preparation:detail', args=[plan.pk]))
        activities = list(plan.sessions.order_by('scheduled_at', 'id'))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len({activity.scheduled_at.date() for activity in activities}), 5)
        current = response.context['current_session']
        self.assertIsNotNone(current)
        self.assertContains(response, current.start_display)
        self.assertContains(response, current.title)

    def test_reschedule_repairs_fourteen_legacy_duplicate_slots(self):
        today = timezone.localdate()
        plan = PreparationPlan.objects.create(
            student=self.student, exam_name='Legacy duplicate schedule',
            deadline=today + timedelta(days=35), syllabus_text='Seven topics',
            study_days=[], preferred_time=time(19, 15), study_hours_per_week=7,
            session_minutes=60, timezone='Asia/Karachi',
        )
        repeated_start = timezone.localtime(timezone.now() + timedelta(days=7), ZoneInfo(plan.timezone)).replace(
            hour=19, minute=15, second=0, microsecond=0,
        )
        kinds = (['study'] * 7) + (['quiz'] * 3) + (['revision'] * 2) + (['mock'] * 2)
        for index, kind in enumerate(kinds):
            LearningSession.objects.create(
                plan=plan, title=f'Activity {index}', kind=kind,
                scheduled_at=repeated_start, scheduled_end_at=repeated_start + timedelta(minutes=60),
                duration_minutes=60,
            )

        moved, total = reschedule(plan)
        activities = list(plan.sessions.order_by('scheduled_at'))
        self.assertEqual((moved, total, len(activities)), (14, 14, 14))
        self.assertEqual(len({activity.scheduled_at for activity in activities}), 14)
        self.assertGreaterEqual((activities[-1].scheduled_at.date() - activities[0].scheduled_at.date()).days, 21)
        for previous, current in zip(activities, activities[1:]):
            self.assertLessEqual(previous.scheduled_end_at, current.scheduled_at)
        first_by_kind = {kind: next(activity.scheduled_at for activity in activities if activity.kind == kind)
                         for kind in ('study', 'quiz', 'revision', 'mock')}
        self.assertLess(first_by_kind['study'], first_by_kind['quiz'])
        self.assertLess(first_by_kind['quiz'], first_by_kind['revision'])
        self.assertLess(first_by_kind['revision'], first_by_kind['mock'])

    def test_reschedule_keeps_completed_session_and_duration(self):
        today = timezone.localdate()
        plan = PreparationPlan.objects.create(
            student=self.student, exam_name='Rescheduling test', deadline=today + timedelta(days=14),
            syllabus_text='Topic', study_hours_per_week=7, preferred_time=time(18, 0),
            session_minutes=60, timezone='Asia/Karachi',
        )
        tz = ZoneInfo(plan.timezone)
        start = timezone.now() + timedelta(days=2)
        start = start.astimezone(tz).replace(hour=18, minute=0, second=0, microsecond=0)
        completed = LearningSession.objects.create(
            plan=plan, title='Completed', scheduled_at=start, scheduled_end_at=start + timedelta(minutes=45),
            duration_minutes=45, status=LearningSession.Status.COMPLETED, completed_at=timezone.now(),
        )
        pending = LearningSession.objects.create(
            plan=plan, title='Pending', scheduled_at=start, scheduled_end_at=start + timedelta(minutes=60),
            duration_minutes=60,
        )
        completed_start = completed.scheduled_at
        moved, total = reschedule(plan)
        completed.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual((moved, total), (1, 1))
        self.assertEqual(completed.scheduled_at, completed_start)
        self.assertEqual(completed.status, LearningSession.Status.COMPLETED)
        no_overlap = (
            pending.scheduled_end_at <= completed.scheduled_at
            or pending.scheduled_at >= completed.scheduled_end_at
        )
        self.assertTrue(no_overlap)
        self.assertEqual(pending.scheduled_end_at - pending.scheduled_at, timedelta(minutes=60))

    @patch('apps.preparation.services.chat_json')
    def test_notes_generate_and_revisit_from_preparation_activity(self, chat_json):
        chat_json.return_value = self.response_for('Medium', 1)
        self.client.post(reverse('preparation:create'), {
            'exam_name': 'Notes workflow exam', 'exam_type': 'Final',
            'deadline': (timezone.localdate() + timedelta(days=21)).isoformat(),
            'syllabus_text': 'Topic 1', 'learning_level': 'intermediate',
            'study_hours_per_week': 7, 'preferred_time': '18:00', 'session_minutes': 60,
            'timezone': 'Asia/Karachi',
        })
        topic = PreparationTopic.objects.get(plan__exam_name='Notes workflow exam').learning_topic
        note_content = {
            'introduction': 'Intro', 'explanation': 'Detailed explanation', 'key_terms': [],
            'examples': [], 'summary': 'Summary',
        }
        with patch('apps.learning.views.generate_notes', return_value=note_content):
            response = self.client.post(reverse('learning:notes_generate', args=[topic.pk]), {'length': 'medium'})
        self.assertEqual(response.status_code, 302)
        detail = self.client.get(reverse('preparation:detail', args=[PreparationPlan.objects.get(exam_name='Notes workflow exam').pk]))
        self.assertContains(detail, 'Revisit AI notes')

    @patch('apps.preparation.services.chat_json')
    def test_practice_quiz_is_linked_and_updates_plan_after_submission(self, chat_json):
        chat_json.return_value = self.response_for('Medium', 1)
        response = self.client.post(reverse('preparation:create'), {
            'exam_name': 'Quiz workflow exam', 'exam_type': 'Final',
            'deadline': (timezone.localdate() + timedelta(days=21)).isoformat(),
            'syllabus_text': 'Topic 1', 'learning_level': 'intermediate',
            'study_hours_per_week': 7, 'preferred_time': '18:00', 'session_minutes': 60,
            'timezone': 'Asia/Karachi',
        })
        plan = PreparationPlan.objects.get(exam_name='Quiz workflow exam')
        topic = plan.topics.get()
        session = plan.sessions.get()
        self.client.post(reverse('preparation:start_session', args=[plan.pk, session.pk]))

        generated = [{
            'question': f'Question {index}?', 'option_a': 'Correct', 'option_b': 'B',
            'option_c': 'C', 'option_d': 'D', 'correct_answer': 'A',
            'explanation': 'Review the core idea.',
        } for index in range(5)]
        with patch('apps.quizzes.services.generate_questions_with_status', return_value=(generated, False, None)):
            response = self.client.post(reverse('preparation:practice_quiz', args=[plan.pk, session.pk]))
        self.assertEqual(response.status_code, 302)
        from apps.quizzes.models import Quiz
        quiz = Quiz.objects.get(student=self.student, preparation_plan=plan, preparation_topic=topic)
        self.assertEqual(quiz.preparation_session, session)
        answers = {f"question_{answer.question_id}": 'A' for answer in quiz.answers.all()}
        for question_id in list(answers)[2:]:
            answers[question_id] = 'B'
        with patch('apps.preparation.services.chat_json', return_value={'recommendations': 'Review the missed concepts.'}):
            result = self.client.post(reverse('quizzes:submit', args=[quiz.pk]), answers)
        quiz.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(result.status_code, 302)
        self.assertTrue(quiz.completed)
        self.assertEqual(quiz.percentage, 40)
        self.assertEqual(session.status, LearningSession.Status.COMPLETED)
        self.assertGreater(plan.readiness_history.count(), 0)
        detail = self.client.get(reverse('preparation:detail', args=[plan.pk]))
        self.assertContains(detail, 'Recent practice')
        self.assertContains(detail, '40%')
        self.assertContains(detail, 'Needs more practice')
        self.assertContains(detail, 'Topic 1')
        result_page = self.client.get(reverse('quizzes:result', args=[quiz.pk]))
        self.assertContains(result_page, 'Correct answer:')
        self.assertContains(result_page, 'Review these missed questions')
        self.assertContains(result_page, 'Return to exam plan')
