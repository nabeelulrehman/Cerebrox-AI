from django.urls import path
from . import views as v

app_name = 'class_quizzes'

urlpatterns = [
    # teacher
    path('teach/class/<int:class_id>/new/', v.quiz_create, name='create'),
    path('teach/scan/', v.scanner, name='scanner'),
    path('teach/verify/', v.verify, name='verify'),
    path('teach/<int:quiz_id>/', v.teacher_detail, name='teacher_detail'),
    path('teach/<int:quiz_id>/schedule/', v.schedule_edit, name='schedule_edit'),
    path('teach/<int:quiz_id>/questions/add/', v.question_add, name='question_add'),
    path('teach/questions/<int:question_id>/delete/', v.question_delete, name='question_delete'),
    path('teach/<int:quiz_id>/ai-generate/', v.ai_generate, name='ai_generate'),
    path('teach/<int:quiz_id>/status/', v.set_status, name='set_status'),
    path('teach/<int:quiz_id>/email/', v.email_link, name='email_link'),
    path('teach/<int:quiz_id>/report/', v.report, name='report'),
    path('teach/<int:quiz_id>/release/', v.release, name='release'),
    path('teach/attempts/<int:attempt_id>/', v.attempt_review, name='attempt_review'),
    path('teach/answers/<int:answer_id>/grade/', v.grade_answer, name='grade_answer'),
    path('teach/answers/<int:answer_id>/ai-suggest/', v.ai_suggest_view, name='ai_suggest'),
    path('teach/answers/<int:answer_id>/accept-ai/', v.accept_ai, name='accept_ai'),
    # student
    path('mine/', v.my_quizzes, name='mine'),
    path('attempts/<int:attempt_id>/', v.take, name='take'),
    path('attempts/<int:attempt_id>/status/', v.status, name='status'),
    path('attempts/<int:attempt_id>/save/', v.save, name='save'),
    path('attempts/<int:attempt_id>/event/', v.event, name='event'),
    path('attempts/<int:attempt_id>/submit/', v.submit, name='submit'),
    path('attempts/<int:attempt_id>/result/', v.result, name='result'),
    path('q/<uuid:token>/', v.entry, name='entry'),
    path('q/<uuid:token>/start/', v.start, name='start'),
]
