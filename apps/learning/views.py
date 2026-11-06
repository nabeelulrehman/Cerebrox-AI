import json
import logging
import time

from django.contrib import messages
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import Course, Topic, AINote
from .forms import CourseForm, TopicForm, NotesGenerateForm, NotesSectionForm
from apps.ai_engine.notes_generator import (
    NotesGenerationInProgress,
    generate_notes,
    generate_notes_stream,
    notes_generation_lock,
)
from apps.ai_engine.gemini_client import AIUnavailableError
from apps.accounts.permissions import student_required

logger = logging.getLogger(__name__)


def _notes_event(event_type, **payload):
    return f'data: {json.dumps({"type": event_type, **payload}, ensure_ascii=False)}\n\n'


# ---------- Courses (student-owned, no predefined/global list) ----------

@student_required
def courses_list(request):
    if request.method == 'POST':
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save(commit=False)
            course.student = request.user
            course.save()
            raw_topics = form.cleaned_data.get('topics_raw', '') or ''
            for raw_name in raw_topics.replace('\r', '').split('\n'):
                for value in raw_name.split(','):
                    name = value.strip()
                    if name:
                        Topic.objects.get_or_create(course=course, name=name)
            messages.success(request, f'Course "{course.name}" added.')
            return redirect('learning:courses')
    else:
        form = CourseForm()
    return render(request, 'student/courses.html', {
        'courses': Course.objects.filter(student=request.user),
        'form': form,
        'open_add_dialog': request.GET.get('add') == '1',
    })


@student_required
def course_edit(request, course_id):
    course = get_object_or_404(Course, pk=course_id, student=request.user)
    form = CourseForm(request.POST or None, instance=course)
    if request.method == 'POST' and form.is_valid():
        form.save()
        raw_topics = form.cleaned_data.get('topics_raw', '') or ''
        for raw_name in raw_topics.replace('\r', '').split('\n'):
            for value in raw_name.split(','):
                name = value.strip()
                if name:
                    Topic.objects.get_or_create(course=course, name=name)
        messages.success(request, 'Course updated.')
        return redirect('learning:courses')
    initial_topics = ', '.join(topic.name for topic in course.topics.all())
    if form.instance and form.instance.pk:
        form.fields['topics_raw'].initial = initial_topics
    return render(request, 'student/courses.html', {
        'courses': Course.objects.filter(student=request.user), 'form': form, 'editing': course,
    })


@student_required
@require_POST
def course_delete(request, course_id):
    get_object_or_404(Course, pk=course_id, student=request.user).delete()
    messages.info(request, 'Course deleted.')
    return redirect('learning:courses')


# ---------- Topics (within one of the student's own courses) ----------

@student_required
def topics_list(request, course_id):
    course = get_object_or_404(Course, pk=course_id, student=request.user)
    if request.method == 'POST':
        form = TopicForm(request.POST)
        if form.is_valid():
            topic = form.save(commit=False)
            topic.course = course
            topic.save()
            messages.success(request, f'Topic "{topic.name}" added.')
            return redirect('learning:topics', course_id=course.id)
    else:
        form = TopicForm()
    return render(request, 'student/topics.html', {
        'course': course, 'topics': course.topics.all(), 'form': form,
    })


@student_required
def topic_edit(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id, course__student=request.user)
    form = TopicForm(request.POST or None, instance=topic)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Topic updated.')
        return redirect('learning:topics', course_id=topic.course_id)
    return render(request, 'student/topics.html', {
        'course': topic.course, 'topics': topic.course.topics.all(), 'form': form, 'editing': topic,
    })


@student_required
@require_POST
def topic_delete(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id, course__student=request.user)
    course_id = topic.course_id
    topic.delete()
    messages.info(request, 'Topic deleted.')
    return redirect('learning:topics', course_id=course_id)


@student_required
def topic_detail(request, topic_id):
    topic = get_object_or_404(Topic.objects.select_related('course'), pk=topic_id, course__student=request.user)
    notes = AINote.objects.filter(student=request.user, topic=topic)[:5]
    return render(request, 'student/topic_detail.html', {'topic': topic, 'notes': notes})


# ---------- AI Notes (Course + Topic + Study Level → AI) ----------

@student_required
def notes_dashboard(request):
    post_data = None
    raw_topic_id = None
    if request.method == 'POST':
        post_data = request.POST.copy()
        if 'course' not in post_data and 'course_id' in post_data:
            post_data['course'] = post_data['course_id']
        if 'topic' not in post_data and 'topic_id' in post_data:
            post_data['topic'] = post_data['topic_id']
        if 'topic_id' in post_data:
            raw_topic_id = post_data['topic_id']
    form = NotesSectionForm(post_data or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        course = form.cleaned_data['course']
        selected_topic = form.cleaned_data['topic']
        if raw_topic_id and not selected_topic:
            selected_topic = Topic.objects.filter(pk=raw_topic_id, course__student=request.user).first()
        topic_name = (form.cleaned_data.get('topic_name') or '').strip()
        if not selected_topic and topic_name:
            topic, _ = Topic.objects.get_or_create(course=course, name=topic_name)
            selected_topic = topic
        if not selected_topic:
            messages.error(request, 'Select an existing topic or enter a new topic name.')
            return render(request, 'student/notes_section.html', {'form': form, 'courses': Course.objects.filter(student=request.user)})
        try:
            with notes_generation_lock(request.user.pk, selected_topic.pk, form.cleaned_data['length']):
                content = generate_notes(
                    course.name, selected_topic.name,
                    request.user.effective_study_level, form.cleaned_data['length'],
                )
                note = AINote.objects.create(
                    student=request.user,
                    topic=selected_topic,
                    study_level=request.user.effective_study_level,
                    content=content,
                )
        except NotesGenerationInProgress as exc:
            messages.error(request, str(exc))
            return render(request, 'student/notes_section.html', {'form': form, 'courses': Course.objects.filter(student=request.user)})
        except AIUnavailableError as exc:
            messages.error(request, 'Unable to generate AI notes right now. Please check the AI configuration and try again.')
            return render(request, 'student/notes_section.html', {'form': form, 'courses': Course.objects.filter(student=request.user), 'error': str(exc)})
        messages.success(request, 'Notes generated and saved.')
        return redirect('learning:notes_view', note_id=note.id)
    return render(request, 'student/notes_section.html', {'form': form, 'courses': Course.objects.filter(student=request.user)})


@student_required
def notes_generate(request, topic_id):
    topic = get_object_or_404(Topic.objects.select_related('course'), pk=topic_id, course__student=request.user)
    if request.method == 'POST':
        form = NotesGenerateForm(request.POST)
        if form.is_valid():
            try:
                with notes_generation_lock(request.user.pk, topic.pk, form.cleaned_data['length']):
                    content = generate_notes(
                        topic.course.name, topic.name,
                        request.user.effective_study_level, form.cleaned_data['length'],
                    )
                    note = AINote.objects.create(
                        student=request.user, topic=topic,
                        study_level=request.user.effective_study_level, content=content,
                    )
            except NotesGenerationInProgress as exc:
                messages.error(request, str(exc))
                return render(request, 'student/notes_generator.html', {'topic': topic, 'form': form})
            except AIUnavailableError as exc:
                messages.error(request, 'Unable to generate AI notes right now. Please check the AI configuration and try again.')
                return render(request, 'student/notes_generator.html', {'topic': topic, 'form': form, 'error': str(exc)})
            messages.success(request, 'Notes generated.')
            return redirect('learning:notes_view', note_id=note.id)
    else:
        form = NotesGenerateForm()
    return render(request, 'student/notes_generator.html', {'topic': topic, 'form': form})


@student_required
@require_POST
def notes_generate_stream(request):
    topic_id = request.POST.get('topic_id')
    if topic_id:
        topic = get_object_or_404(
            Topic.objects.select_related('course'),
            pk=topic_id,
            course__student=request.user,
        )
        form = NotesGenerateForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'error': 'Choose a valid notes length and try again.'}, status=400)
    else:
        form = NotesSectionForm(request.POST, user=request.user)
        if not form.is_valid():
            return JsonResponse({'error': 'Check the course, topic, and notes length, then try again.'}, status=400)
        course = form.cleaned_data['course']
        topic = form.cleaned_data['topic']
        topic_name = (form.cleaned_data.get('topic_name') or '').strip()
        if not topic and topic_name:
            topic, _ = Topic.objects.get_or_create(course=course, name=topic_name)
        if not topic:
            return JsonResponse({'error': 'Select an existing topic or enter a topic name.'}, status=400)

    length = form.cleaned_data['length']
    study_level = request.user.effective_study_level

    def generate_events():
        started = time.perf_counter()
        try:
            with notes_generation_lock(request.user.pk, topic.pk, length):
                yield _notes_event('status', message='Analyzing topic')
                yield _notes_event('status', message='Generating notes')
                stream = generate_notes_stream(topic.course.name, topic.name, study_level, length)
                while True:
                    try:
                        chunk = next(stream)
                    except StopIteration as completed:
                        content = completed.value
                        break
                    yield _notes_event('chunk', text=chunk)

                yield _notes_event('status', message='Preparing your study material')
                note = AINote.objects.create(
                    student=request.user,
                    topic=topic,
                    study_level=study_level,
                    content=content,
                )
                logger.info(
                    'AI notes generated and saved: topic_id=%s elapsed_ms=%s',
                    topic.pk,
                    round((time.perf_counter() - started) * 1000),
                )
                yield _notes_event(
                    'complete',
                    url=reverse('learning:notes_view', kwargs={'note_id': note.pk}),
                )
        except NotesGenerationInProgress as exc:
            yield _notes_event('error', message=str(exc))
        except AIUnavailableError as exc:
            yield _notes_event('error', message=str(exc))
        except Exception:
            logger.exception('AI notes streaming failed: topic_id=%s', topic.pk)
            yield _notes_event(
                'error',
                message='Notes could not be completed. Please retry.',
            )

    response = StreamingHttpResponse(
        generate_events(),
        content_type='text/event-stream; charset=utf-8',
    )
    response['Cache-Control'] = 'no-cache, no-transform'
    response['X-Accel-Buffering'] = 'no'
    return response


@student_required
def notes_view(request, note_id):
    note = get_object_or_404(AINote, pk=note_id, student=request.user)
    return render(request, 'student/generated_notes.html', {'note': note})
