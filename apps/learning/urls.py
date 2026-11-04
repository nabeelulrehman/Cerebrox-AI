from django.urls import path
from . import views

app_name = 'learning'

urlpatterns = [
    path('courses/', views.courses_list, name='courses'),
    path('courses/<int:course_id>/edit/', views.course_edit, name='course_edit'),
    path('courses/<int:course_id>/delete/', views.course_delete, name='course_delete'),

    path('courses/<int:course_id>/topics/', views.topics_list, name='topics'),
    path('topics/<int:topic_id>/edit/', views.topic_edit, name='topic_edit'),
    path('topics/<int:topic_id>/delete/', views.topic_delete, name='topic_delete'),
    path('topics/<int:topic_id>/', views.topic_detail, name='topic_detail'),

    path('notes/', views.notes_dashboard, name='notes_dashboard'),
    path('topics/<int:topic_id>/notes/generate/', views.notes_generate, name='notes_generate'),
    path('notes/generate/stream/', views.notes_generate_stream, name='notes_generate_stream'),
    path('notes/<int:note_id>/', views.notes_view, name='notes_view'),
]
