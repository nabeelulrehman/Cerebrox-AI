from django.urls import path
from . import views

app_name = 'teachers'

urlpatterns = [
    path('register/', views.teacher_register, name='register'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('profile/', views.profile_edit, name='profile_edit'),
    path('directory/', views.directory, name='directory'),
    path('directory/<int:teacher_id>/', views.teacher_public_profile, name='public_profile'),
]
