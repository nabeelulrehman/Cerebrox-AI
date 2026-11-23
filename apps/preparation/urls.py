from django.urls import path
from . import views

app_name = 'preparation'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('notifications/<int:notification_id>/read/', views.notification_read, name='notification_read'),
    path('new/', views.plan_create, name='create'),
    path('<int:plan_id>/', views.plan_detail, name='detail'),
    path('<int:plan_id>/availability/', views.update_availability, name='availability'),
    path('<int:plan_id>/reschedule/', views.reschedule_preview, name='reschedule'),
    path('<int:plan_id>/reschedule/apply/', views.reschedule_apply, name='reschedule_apply'),
    path('<int:plan_id>/sessions/<int:session_id>/start/', views.start_session, name='start_session'),
    path('<int:plan_id>/sessions/<int:session_id>/complete/', views.complete_session, name='complete_session'),
    path('<int:plan_id>/sessions/<int:session_id>/practice-quiz/', views.practice_quiz, name='practice_quiz'),
    path('<int:plan_id>/sessions/<int:session_id>/notes/generate/', views.notes_generate, name='notes_generate'),
    path('<int:plan_id>/sessions/<int:session_id>/notes/<int:note_id>/', views.notes_view, name='notes_view'),
    path('quizzes/<int:quiz_id>/take/', views.quiz_take, name='quiz_take'),
    path('quizzes/<int:quiz_id>/submit/', views.quiz_submit, name='quiz_submit'),
    path('quizzes/<int:quiz_id>/result/', views.quiz_result, name='quiz_result'),
    path('<int:plan_id>/assessments/add/', views.assessment_add, name='assessment_add'),
]
