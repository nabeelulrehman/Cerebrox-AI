from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404

from .forms import QuizGenerateForm, QuizSectionForm
from .models import Quiz
from .services import QuizGenerationInProgress, create_ai_quiz
from .evaluation import submit_quiz
from apps.learning.models import Course, Topic
from apps.accounts.permissions import student_required


def _announce(request, quiz):
    if getattr(quiz, 'used_fallback', False):
        error = (getattr(quiz, 'generation_error', '') or '').lower()
        if 'timed out' in error or 'timeout' in error:
            message = (
                'Gemini timed out. These are generic PLACEHOLDER questions, not real study material. '
                'Please retry later for an AI-generated quiz.'
            )
        elif 'api_key_invalid' in error or 'api key' in error:
            message = (
                'Gemini could not authenticate. These are generic PLACEHOLDER questions, not real '
                'study material. Check the backend Gemini configuration and retry.'
            )
        else:
            message = (
                'Gemini is temporarily unavailable. These are generic PLACEHOLDER questions, not '
                'real study material. Please retry later for an AI-generated quiz.'
            )
        messages.warning(request, message)
    else:
        messages.success(request, f'{quiz.total_questions} questions ready — good luck!')


@student_required
def quiz_dashboard(request):
    post_data = None
    raw_topic_ids = []
    if request.method == 'POST':
        post_data = request.POST.copy()
        if 'course' not in post_data and 'course_id' in post_data:
            post_data['course'] = post_data['course_id']
        if 'topics' not in post_data and 'topic_ids' in post_data:
            post_data.setlist('topics', post_data.getlist('topic_ids'))
        raw_topic_ids = [value for value in post_data.getlist('topic_ids') if value]
    form = QuizSectionForm(post_data or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        course = form.cleaned_data['course']
        selected_topics = list(form.cleaned_data['topics'])
        if raw_topic_ids:
            topic_ids = list(dict.fromkeys(int(pk) for pk in raw_topic_ids if pk.isdecimal()))
            topic_map = Topic.objects.filter(
                pk__in=topic_ids,
                course__student=request.user,
            ).select_related('course').in_bulk()
            selected_topics = [
                topic_map[topic_id] for topic_id in topic_ids if topic_id in topic_map
            ]
        if not selected_topics and course:
            selected_topics = list(course.topics.select_related('course').all())
        if not selected_topics:
            messages.error(request, 'Select at least one topic for the quiz.')
            return render(request, 'student/quiz_dashboard.html', {'form': form, 'courses': Course.objects.filter(student=request.user)})
        primary_topic = selected_topics[0]
        try:
            quiz = create_ai_quiz(
                request.user,
                primary_topic,
                form.cleaned_data['difficulty'],
                form.cleaned_data['num_questions'],
                extra_topics=selected_topics[1:],
            )
        except QuizGenerationInProgress:
            messages.info(request, 'This quiz is already being generated. Please wait before trying again.')
            return render(request, 'student/quiz_dashboard.html', {
                'form': form,
                'courses': Course.objects.filter(student=request.user),
            })
        _announce(request, quiz)
        return redirect('quizzes:take', quiz_id=quiz.id)
    return render(request, 'student/quiz_dashboard.html', {'form': form, 'courses': Course.objects.filter(student=request.user)})


@student_required
def quiz_generate(request, topic_id):
    topic = get_object_or_404(Topic.objects.select_related('course'), pk=topic_id, course__student=request.user)
    if request.method == 'POST':
        form = QuizGenerateForm(request.POST)
        if form.is_valid():
            try:
                quiz = create_ai_quiz(
                    request.user, topic,
                    form.cleaned_data['difficulty'], form.cleaned_data['num_questions'],
                )
            except QuizGenerationInProgress:
                messages.info(request, 'This quiz is already being generated. Please wait before trying again.')
                return render(request, 'student/quiz_generator.html', {'topic': topic, 'form': form})
            _announce(request, quiz)
            return redirect('quizzes:take', quiz_id=quiz.id)
    else:
        form = QuizGenerateForm()
    return render(request, 'student/quiz_generator.html', {'topic': topic, 'form': form})


@student_required
def quiz_take(request, quiz_id):
    quiz = get_object_or_404(Quiz, pk=quiz_id, student=request.user)
    if quiz.completed:
        return redirect('quizzes:result', quiz_id=quiz.id)
    answers = quiz.answers.select_related('question').order_by('id')
    return render(request, 'student/quiz.html', {'quiz': quiz, 'answers': answers})


@student_required
def quiz_submit(request, quiz_id):
    quiz = get_object_or_404(Quiz, pk=quiz_id, student=request.user)
    if request.method == 'POST' and not quiz.completed:
        answer_map = {}
        for qa in quiz.answers.all():
            key = f'question_{qa.question_id}'
            if key in request.POST:
                answer_map[str(qa.question_id)] = request.POST[key]
        submit_quiz(quiz, answer_map)
        messages.success(request, 'Quiz submitted and evaluated.')
    return redirect('quizzes:result', quiz_id=quiz.id)


@student_required
def quiz_result(request, quiz_id):
    quiz = get_object_or_404(Quiz, pk=quiz_id, student=request.user)
    if not quiz.completed:
        return redirect('quizzes:take', quiz_id=quiz.id)
    answers = quiz.answers.select_related('question').order_by('id')
    return render(request, 'student/quiz_result.html', {'quiz': quiz, 'answers': answers})


@student_required
def quiz_history(request):
    quizzes = Quiz.objects.filter(student=request.user, completed=True)
    return render(request, 'student/performance.html', {'quizzes': quizzes})
