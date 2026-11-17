from datetime import datetime, time, timedelta, timezone as datetime_timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.utils import timezone
from django.db.models import Q
from django.db import transaction
from apps.ai_engine.gemini_client import chat_json
from .models import PreparationTopic, LearningSession, PreparationAssessment, ReadinessSnapshot
from apps.learning.models import Topic as LearningTopic, Course
from apps.quizzes.models import Quiz


PRIORITY_LABELS = {
    'very low': 1,
    'low': 1,
    'medium': 3,
    'high': 5,
    'very high': 5,
}
ACTIVITY_KIND_ORDER = {
    LearningSession.Kind.STUDY: 0,
    LearningSession.Kind.QUIZ: 1,
    LearningSession.Kind.REVISION: 2,
    LearningSession.Kind.MOCK: 3,
}


def _priority_value(value):
    """Convert an importance label or a validated numeric rank to the DB's 1–5 scale."""
    if isinstance(value, bool):
        raise ValueError('Topic priority must be Low, Medium, High, or a number from 1 to 5.')
    if isinstance(value, (int, float)):
        if 1 <= value <= 5 and float(value).is_integer():
            return int(value)
        raise ValueError('Numeric topic priority must be between 1 and 5.')
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in PRIORITY_LABELS:
            return PRIORITY_LABELS[normalized]
        if re.fullmatch(r'[1-5]', normalized):
            return int(normalized)
    raise ValueError('Topic priority must be Low, Medium, High, or a number from 1 to 5.')


def _day_offset(value, fallback):
    """Accept only an integer day offset; never coerce descriptive text into a number."""
    if value is None:
        return fallback
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and re.fullmatch(r'\s*\d+\s*', value):
        return int(value.strip())
    return fallback


def _response_text(value, field, *, limit):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError(f'AI returned an invalid {field} value in the study plan.')
    return value.strip()[:limit]


def _validate_plan_response(response):
    """Validate the complete Gemini structure before writing any plan topics or sessions."""
    if not isinstance(response, dict):
        raise ValueError('AI returned an invalid study plan response.')
    raw_topics, raw_sessions = response.get('topics'), response.get('sessions')
    if not isinstance(raw_topics, list) or not raw_topics or not isinstance(raw_sessions, list) or not raw_sessions:
        raise ValueError('AI returned an incomplete study schedule. Try again with a clearer syllabus.')
    if len(raw_topics) > 100 or len(raw_sessions) > 300:
        raise ValueError('AI returned too many topics or sessions. Regenerate the plan with a shorter syllabus.')

    topics = []
    names = set()
    for item in raw_topics:
        if not isinstance(item, dict):
            raise ValueError('AI returned an invalid topic in the study plan.')
        name = _response_text(item.get('name'), 'topic name', limit=180)
        if not name:
            raise ValueError('AI returned a topic without a name.')
        key = name.casefold()
        if key in names:
            raise ValueError('AI returned duplicate topic names. Regenerate the plan.')
        names.add(key)
        # The schema stores priority as a numeric 1–5 rank. Gemini may express it
        # as Low/Medium/High, so normalize those labels explicitly before saving.
        raw_priority = item.get('priority', item.get('topic_importance', item.get('importance', 3)))
        topics.append({
            'name': name,
            'description': _response_text(item.get('description', ''), 'topic description', limit=3000),
            'priority': _priority_value(raw_priority),
        })

    sessions = []
    for index, item in enumerate(raw_sessions):
        if not isinstance(item, dict):
            raise ValueError('AI returned an invalid learning session in the study plan.')
        topic_name = _response_text(item.get('topic', ''), 'session topic', limit=180).casefold()
        if topic_name and topic_name not in names:
            raise ValueError('AI returned a session for a topic that is not in the syllabus plan.')
        kind = item.get('kind', LearningSession.Kind.STUDY)
        if not isinstance(kind, str) or kind not in LearningSession.Kind.values:
            kind = LearningSession.Kind.STUDY
        sessions.append({
            'topic': topic_name,
            'kind': kind,
            'title': _response_text(item.get('title', ''), 'session title', limit=180),
            'objectives': _response_text(item.get('objectives', ''), 'session objectives', limit=3000),
            'material': _response_text(item.get('material', ''), 'session material', limit=5000),
            'day_offset': _day_offset(item.get('day_offset'), index),
        })
    if not sessions:
        raise ValueError('AI returned no usable learning sessions.')
    return topics, sessions


def extract_syllabus(upload):
    """Extract supported document text in memory; no uploaded source is made public or persisted."""
    ext = upload.name.rsplit('.', 1)[-1].lower()
    raw = upload.read()
    if ext == 'pdf':
        from pypdf import PdfReader
        from io import BytesIO
        return '\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(raw)).pages)[:30000]
    if ext in {'docx', 'pptx'}:
        from io import BytesIO
        if ext == 'docx':
            from docx import Document
            return '\n'.join(p.text for p in Document(BytesIO(raw)).paragraphs)[:30000]
        from pptx import Presentation
        return '\n'.join(shape.text for slide in Presentation(BytesIO(raw)).slides for shape in slide.shapes if hasattr(shape, 'text'))[:30000]
    if ext in {'doc', 'ppt'}:
        raise ValueError('Legacy DOC/PPT files must be saved as DOCX/PPTX before upload.')
    if ext in {'png', 'jpg', 'jpeg', 'webp'}:
        from google.genai import types
        mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp'}[ext]
        response = chat_json(
            'Read the syllabus image and return JSON with a string field named text. Transcribe only readable educational text.',
            ['Extract the syllabus topics from this image.', types.Part.from_bytes(data=raw, mime_type=mime)],
            max_output_tokens=3000,
        )
        return str(response.get('text', ''))[:30000]
    return raw.decode('utf-8', errors='ignore')[:30000]


def generate_plan(plan):
    response = chat_json(
        'You design realistic study plans. Return JSON with topics: [{name,description,priority}] and sessions: '
        '[{topic,kind,title,objectives,material,day_offset}]. Topic priority MUST be an integer from 1 to 5 '
        '(1=low importance, 3=medium, 5=high); do not return words such as Low, Medium, or High for priority. '
        'Use only topic names from the provided syllabus. Schedule study, quiz, revision and mock activities. '
        'Keep session workload within the stated weekly hours. Do not return dates or timestamps; the backend '
        'assigns each activity a real slot from the student availability and exam deadline. '
        'Order learning as study first, topic quiz after study, revision after quiz, and mock examinations near the deadline.',
        f'Exam: {plan.exam_name} ({plan.exam_type}). Deadline: {plan.deadline}. Level: {plan.learning_level}. '
        f'Hours/week: {plan.study_hours_per_week}. Weekdays: {plan.study_days or "flexible"}. '
        f'Session minutes: {plan.session_minutes}. Goals: {plan.goals}. Syllabus:\n{plan.syllabus_text[:12000]}',
        max_output_tokens=5000,
    )
    topics, sessions = _validate_plan_response(response)
    ordered_sessions = sorted(
        enumerate(sessions),
        key=lambda pair: (ACTIVITY_KIND_ORDER[pair[1]['kind']], pair[1]['day_offset'], pair[0]),
    )
    ordered_sessions = [item for _, item in ordered_sessions]
    slots = _allocate_schedule(
        plan, [plan.session_minutes] * len(ordered_sessions),
        kinds=[item['kind'] for item in ordered_sessions],
    )

    topic_map = {}
    prep_course = None
    for index, item in enumerate(topics):
        topic_name = item['name']
        existing = LearningTopic.objects.filter(course__student=plan.student, name__iexact=topic_name).first()
        if existing is None:
            if len(topic_name) > 150:
                raise ValueError('A plan topic name is too long to use with the learning and quiz tools.')
            if prep_course is None:
                prep_course, _ = Course.objects.get_or_create(
                    student=plan.student, name='Exam Preparation',
                    defaults={'description': 'Topics used by personalized exam preparation plans.'},
                )
            existing, _ = LearningTopic.objects.get_or_create(
                course=prep_course, name=topic_name,
                defaults={'description': item['description']},
            )
        topic = PreparationTopic.objects.create(plan=plan, learning_topic=existing, name=topic_name,
            description=item['description'], priority=item['priority'], order=index)
        topic_map[topic.name.casefold()] = topic
    made = 0
    for item, (local_dt, local_end) in zip(ordered_sessions, slots):
        target = topic_map.get(item['topic'])
        LearningSession.objects.create(plan=plan, topic=target, title=item['title'] or (target.name if target else 'Study session'),
            objectives=item['objectives'], material=item['material'],
            scheduled_at=local_dt, scheduled_end_at=local_end,
            duration_minutes=plan.session_minutes, kind=item['kind'])
        made += 1
    if not made:
        raise ValueError('No study sessions could be scheduled. Please regenerate the plan.')
    return len(topic_map), made


def _plan_timezone(plan):
    try:
        return ZoneInfo(plan.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo('Asia/Karachi')


def _local_datetime(day, preferred_time, tz):
    naive = datetime.combine(day, preferred_time)
    aware = naive.replace(tzinfo=tz)
    # ZoneInfo permits nonexistent wall times; round-trip through UTC to the next valid local time.
    normalized = aware.astimezone(datetime_timezone.utc).astimezone(tz)
    return normalized if normalized.replace(tzinfo=None) != naive else aware


def _add_elapsed_minutes(value, minutes):
    return (value.astimezone(datetime_timezone.utc) + timedelta(minutes=minutes)).astimezone(value.tzinfo)


def _allocate_schedule(plan, durations, *, kinds=None, occupied_sessions=()):
    """Distribute staged activities over available dates within daily and weekly capacity."""
    tz = _plan_timezone(plan)
    now = timezone.localtime(timezone.now(), tz)
    today = now.date()
    selected_days = {int(day) for day in plan.study_days if str(day).isdigit() and 0 <= int(day) <= 6}
    available_days = selected_days or set(range(7))
    weekly_budget = plan.study_hours_per_week * 60
    daily_budget = max(plan.session_minutes, weekly_budget // len(available_days))
    preferred = plan.preferred_time or time(18, 0)
    if kinds is None:
        kinds = [LearningSession.Kind.STUDY] * len(durations)
    if len(kinds) != len(durations):
        raise ValueError('Each activity must have a matching schedule type.')
    if any(not isinstance(duration, int) or duration <= 0 for duration in durations):
        raise ValueError('Study activity durations must be positive whole minutes.')
    if any(duration > weekly_budget for duration in durations):
        raise ValueError('An activity is longer than your total weekly study availability.')

    eligible_dates = [
        today + timedelta(days=offset)
        for offset in range(max(0, (plan.deadline - today).days) + 1)
        if (today + timedelta(days=offset)).weekday() in available_days
    ]
    if not eligible_dates:
        raise ValueError('There are no selected study days before the exam deadline.')

    present_kinds = sorted(set(kinds), key=ACTIVITY_KIND_ORDER.__getitem__)
    anchors = [None] * len(durations)
    phase_position = 0
    for kind in present_kinds:
        indices = [index for index, value in enumerate(kinds) if value == kind]
        phase_start = phase_position / len(durations)
        phase_position += len(indices)
        phase_end = phase_position / len(durations)
        for position, index in enumerate(indices):
            fraction = phase_start + (phase_end - phase_start) * (position + 0.5) / len(indices)
            anchors[index] = eligible_dates[round(fraction * (len(eligible_dates) - 1))]

    occupied_by_day = {}
    daily_minutes = {}
    weekly_minutes = {}
    occupied_sessions = list(occupied_sessions)
    for session in occupied_sessions:
        start = timezone.localtime(session.scheduled_at, tz)
        end = timezone.localtime(session.scheduled_end_at, tz) if session.scheduled_end_at else start + timedelta(minutes=session.duration_minutes)
        occupied_by_day.setdefault(start.date(), []).append((start, end))
        daily_minutes[start.date()] = daily_minutes.get(start.date(), 0) + session.duration_minutes
        iso_year, iso_week, _ = start.isocalendar()
        week_key = (iso_year, iso_week)
        weekly_minutes[week_key] = weekly_minutes.get(week_key, 0) + session.duration_minutes

    slots = []
    day = today
    for index, duration in enumerate(durations):
        day = max(day, anchors[index])
        placed = None
        while day <= plan.deadline:
            if day.weekday() not in available_days:
                day += timedelta(days=1)
                continue
            day_start = _local_datetime(day, preferred, tz)
            if (day == today and day_start.astimezone(datetime_timezone.utc)
                    <= now.astimezone(datetime_timezone.utc)):
                day_start = now.replace(second=0, microsecond=0) + timedelta(minutes=10)
            candidate = day_start
            for existing_start, existing_end in sorted(
                occupied_by_day.get(day, []),
                key=lambda interval: interval[0].astimezone(datetime_timezone.utc),
            ):
                candidate_end = _add_elapsed_minutes(candidate, duration)
                if (candidate_end.astimezone(datetime_timezone.utc) <= existing_start.astimezone(datetime_timezone.utc)
                        or candidate.astimezone(datetime_timezone.utc) >= existing_end.astimezone(datetime_timezone.utc)):
                    continue
                candidate = _add_elapsed_minutes(existing_end, 15)

            elapsed = daily_minutes.get(day, 0)
            iso_year, iso_week, _ = day.isocalendar()
            week_key = (iso_year, iso_week)
            week_elapsed = weekly_minutes.get(week_key, 0)
            candidate_end = _add_elapsed_minutes(candidate, duration)
            if (elapsed + duration <= daily_budget and week_elapsed + duration <= weekly_budget
                    and candidate.date() == day):
                placed = (candidate, candidate_end)
                occupied_by_day.setdefault(day, []).append(placed)
                daily_minutes[day] = elapsed + duration
                weekly_minutes[week_key] = week_elapsed + duration
                break
            day += timedelta(days=1)
        if placed is None:
            raise ValueError('There is not enough available study time before the exam deadline for every activity.')
        slots.append(placed)
    _validate_allocated_schedule(plan, slots, durations, kinds, occupied_sessions)
    return slots


def repair_collapsed_schedule(plan):
    """Repair legacy plans whose pending activities were all saved on one date.

    This is intentionally narrow: normally scheduled plans are not touched, and
    ``reschedule`` keeps completed activities and their progress unchanged.
    """
    tz = _plan_timezone(plan)
    now = timezone.localtime(timezone.now(), tz)
    pending = list(plan.sessions.filter(
        status__in=[LearningSession.Status.PENDING, LearningSession.Status.MISSED],
    ).order_by('scheduled_at', 'id'))
    if len(pending) < 5:
        return 0, len(pending)
    pending_dates = {timezone.localtime(session.scheduled_at, tz).date() for session in pending}
    if len(pending_dates) != 1:
        return 0, len(pending)
    days = {int(value) for value in plan.study_days if str(value).isdigit() and 0 <= int(value) <= 6}
    days = days or set(range(7))
    eligible_count = sum(
        1 for offset in range(max(0, (plan.deadline - now.date()).days) + 1)
        if (now.date() + timedelta(days=offset)).weekday() in days
    )
    if eligible_count < 5:
        return 0, len(pending)
    try:
        return reschedule(plan)
    except ValueError:
        # Leave the existing rows intact if the remaining time or capacity is
        # insufficient to produce a valid replacement schedule.
        return 0, len(pending)


def _validate_allocated_schedule(plan, slots, durations, kinds, occupied_sessions=()):
    """Reject invalid, duplicate, overlapping, or over-capacity schedule slots before persistence."""
    if len(slots) != len(durations) or len(kinds) != len(durations):
        raise ValueError('The generated schedule is incomplete. Please regenerate the plan.')
    tz = _plan_timezone(plan)
    now = timezone.localtime(timezone.now(), tz)
    today = now.date()
    selected_days = {int(day) for day in plan.study_days if str(day).isdigit() and 0 <= int(day) <= 6}
    available_days = selected_days or set(range(7))
    weekly_budget = plan.study_hours_per_week * 60
    daily_budget = max(plan.session_minutes, weekly_budget // len(available_days))
    daily_minutes = {}
    weekly_minutes = {}
    occupied_by_day = {}
    for session in occupied_sessions:
        start = timezone.localtime(session.scheduled_at, tz)
        end = timezone.localtime(session.scheduled_end_at, tz) if session.scheduled_end_at else _add_elapsed_minutes(start, session.duration_minutes)
        occupied_by_day.setdefault(start.date(), []).append((start, end))
        daily_minutes[start.date()] = daily_minutes.get(start.date(), 0) + session.duration_minutes
        year, week, _ = start.isocalendar()
        weekly_minutes[(year, week)] = weekly_minutes.get((year, week), 0) + session.duration_minutes

    ordered = sorted(zip(slots, durations), key=lambda slot_and_duration: slot_and_duration[0][0].astimezone(datetime_timezone.utc))
    for (start, end), duration in zip(slots, durations):
        if not timezone.is_aware(start) or not timezone.is_aware(end):
            raise ValueError('Generated activity times must include a timezone.')
        local_start = timezone.localtime(start, tz)
        if local_start.date() < today or local_start.date() > plan.deadline or local_start.weekday() not in available_days:
            raise ValueError('A generated activity falls outside your selected study days or exam deadline.')
        actual_duration = int((end.astimezone(datetime_timezone.utc) - start.astimezone(datetime_timezone.utc)).total_seconds() // 60)
        if actual_duration != duration:
            raise ValueError('A generated activity end time does not match its duration.')
        if (local_start.date() == today
                and start.astimezone(datetime_timezone.utc) <= now.astimezone(datetime_timezone.utc)):
            raise ValueError('A generated activity is scheduled in the past.')
        for occupied_start, occupied_end in occupied_by_day.get(local_start.date(), []):
            if (start.astimezone(datetime_timezone.utc) < occupied_end.astimezone(datetime_timezone.utc)
                    and end.astimezone(datetime_timezone.utc) > occupied_start.astimezone(datetime_timezone.utc)):
                raise ValueError('A generated activity overlaps an existing completed activity.')
        daily_minutes[local_start.date()] = daily_minutes.get(local_start.date(), 0) + duration
        year, week, _ = local_start.isocalendar()
        week_key = (year, week)
        weekly_minutes[week_key] = weekly_minutes.get(week_key, 0) + duration
        if daily_minutes[local_start.date()] > daily_budget or weekly_minutes[week_key] > weekly_budget:
            raise ValueError('The generated schedule exceeds your daily or weekly study availability.')
    for ((previous_start, previous_end), _), ((next_start, _), _) in zip(ordered, ordered[1:]):
        previous_start_utc = previous_start.astimezone(datetime_timezone.utc)
        previous_end_utc = previous_end.astimezone(datetime_timezone.utc)
        next_start_utc = next_start.astimezone(datetime_timezone.utc)
        if previous_start_utc == next_start_utc or next_start_utc < previous_end_utc:
            raise ValueError('Generated activities contain duplicate or overlapping time slots.')
    for (previous_start, _), (next_start, _) in zip(slots, slots[1:]):
        if next_start.astimezone(datetime_timezone.utc) < previous_start.astimezone(datetime_timezone.utc):
            raise ValueError('Generated activities are not ordered by their scheduled times.')
    # When the preparation window offers enough study days, a multi-activity
    # plan must be distributed across at least five dates. This catches date
    # collapse while still allowing multiple sequential sessions on a day.
    eligible_dates = {
        today + timedelta(days=offset)
        for offset in range(max(0, (plan.deadline - today).days) + 1)
        if (today + timedelta(days=offset)).weekday() in available_days
    }
    distinct_scheduled_dates = {timezone.localtime(start, tz).date() for start, _ in slots}
    if len(slots) >= 5 and len(eligible_dates) >= 5 and len(distinct_scheduled_dates) < 5:
        raise ValueError('The generated schedule is concentrated on too few dates. Please review your availability or deadline.')
    # Ensure phase ordering remains study, quiz, revision, then mock.
    ranks = [ACTIVITY_KIND_ORDER[kind] for kind in kinds]
    if ranks != sorted(ranks):
        raise ValueError('Generated activities are not ordered by learning stage.')


def readiness(plan):
    """Transparent database-based estimate; assessment evidence weighs far more than completion."""
    topics = list(plan.topics.all())
    if not topics:
        return {'score': 0, 'confidence': 'No data', 'topic_scores': [], 'strong': [], 'weak': [], 'coverage': 0}
    completed_sessions = plan.sessions.filter(status=LearningSession.Status.COMPLETED).count()
    all_sessions = plan.sessions.count()
    coverage = round(100 * completed_sessions / all_sessions) if all_sessions else 0
    scores = []
    for topic in topics:
        results = list(topic.assessments.all()[:10])
        quiz_results = list(Quiz.objects.filter(student=plan.student, completed=True).filter(
            Q(topic=topic.learning_topic) | Q(preparation_topic=topic),
        ).distinct().order_by('-created_at')[:10])
        percentages = [float(a.score / a.possible) * 100 for a in results] + [float(q.percentage) for q in quiz_results]
        score = round(sum(percentages) / len(percentages)) if percentages else 0
        scores.append({'topic': topic, 'score': score, 'evidence': len(percentages), 'completed': bool(topic.completed_at)})
    # Topic assessment performance 70%, completion coverage 15%, assessment participation 15%.
    all_assessments = plan.assessments.count() + Quiz.objects.filter(student=plan.student, completed=True).filter(
        Q(topic__preparation_topics__plan=plan) | Q(preparation_plan=plan),
    ).distinct().count()
    demonstrated = sum(row['score'] for row in scores) / len(scores)
    evidence_factor = min(1.0, all_assessments / max(2, len(topics)))
    score = round(demonstrated * .7 + coverage * .15 + demonstrated * evidence_factor * .15)
    confidence = 'High' if all_assessments >= max(5, len(topics) * 2) else 'Moderate' if all_assessments >= 2 else 'Low'
    return {'score': min(100, score), 'confidence': confidence, 'topic_scores': scores,
        'strong': [r['topic'].name for r in scores if r['evidence'] and r['score'] >= 75],
        'weak': [r['topic'].name for r in scores if r['evidence'] and r['score'] < 60], 'coverage': coverage}


def record_readiness(plan, explanation=''):
    result = readiness(plan)
    return ReadinessSnapshot.objects.create(plan=plan, score=result['score'], confidence=result['confidence'],
                                            explanation=explanation[:240])


def update_recommendations(plan):
    assessments = list(plan.assessments.select_related('topic').all()[:12])
    evidence = [{'topic': item.topic.name if item.topic else 'Overall', 'kind': item.kind,
                 'score': float(item.score), 'possible': float(item.possible),
                 'date': item.created_at.date().isoformat()} for item in assessments]
    recent_quizzes = Quiz.objects.filter(student=plan.student, completed=True).filter(
        Q(topic__preparation_topics__plan=plan) | Q(preparation_plan=plan),
    ).select_related('topic', 'preparation_topic').prefetch_related('answers__question').order_by('-created_at').distinct()[:12]
    evidence.extend({
        'topic': quiz.preparation_topic.name if quiz.preparation_topic_id else quiz.topic.name,
        'kind': 'practice_quiz', 'score': quiz.percentage, 'possible': 100,
        'date': quiz.created_at.date().isoformat(),
        'weak_concepts': [answer.question.question[:180] for answer in quiz.answers.all() if not answer.correct][:4],
    } for quiz in recent_quizzes)
    result = readiness(plan)
    try:
        answer = chat_json(
            'You are an adaptive study coach. Return JSON with a concise string field recommendations.',
            f'Exam: {plan.exam_name}; deadline: {plan.deadline}; readiness: {result["score"]}; '
            f'weak topics: {result["weak"]}; recent assessment records: {evidence}. '
            'Use weak_concepts from missed quiz questions to recommend focused revision or extra practice. '
            'Recommend the next one or two concrete learning activities. Do not invent scores.',
            max_output_tokens=700,
        )
        text = answer.get('recommendations')
        if isinstance(text, str) and text.strip():
            plan.next_recommendations = text.strip()[:3000]
            plan.save(update_fields=['next_recommendations', 'updated_at'])
    except Exception:
        # Assessment saving and deterministic readiness must continue when AI is unavailable.
        return ''
    return plan.next_recommendations


@transaction.atomic
def reschedule(plan):
    """Preserve history and completed sessions; shift outstanding work forward without duplicates."""
    now = timezone.now()
    overdue = plan.sessions.filter(status=LearningSession.Status.PENDING, scheduled_at__lt=now)
    for session in overdue:
        session.history.append({'kind': 'missed', 'scheduled_at': session.scheduled_at.isoformat()})
        session.status = LearningSession.Status.MISSED
        session.save(update_fields=['status', 'history'])
    pending = list(plan.sessions.filter(status=LearningSession.Status.MISSED).order_by('scheduled_at', 'id'))
    pending += list(plan.sessions.filter(status=LearningSession.Status.PENDING).order_by('scheduled_at', 'id'))
    pending.sort(key=lambda session: (ACTIVITY_KIND_ORDER[session.kind], session.scheduled_at, session.pk))
    completed = plan.sessions.filter(status=LearningSession.Status.COMPLETED)
    slots = _allocate_schedule(
        plan, [session.duration_minutes for session in pending],
        kinds=[session.kind for session in pending], occupied_sessions=completed,
    )
    moved = 0
    for session, (when, end_at) in zip(pending, slots):
        old_time = session.scheduled_at
        session.history.append({'kind': 'rescheduled', 'from': old_time.isoformat(), 'to': when.isoformat(), 'end': end_at.isoformat()})
        session.scheduled_at = when
        session.scheduled_end_at = end_at
        session.status = LearningSession.Status.PENDING
        session.save(update_fields=['scheduled_at', 'scheduled_end_at', 'status', 'history'])
        moved += 1
    plan.reschedule_notice = (f'{moved} unfinished activities were moved. ' +
        ('The remaining time is too short for every activity.' if moved < len(pending) else 'Completed progress and assessment records were preserved.'))
    plan.save(update_fields=['reschedule_notice', 'updated_at'])
    return moved, len(pending)
