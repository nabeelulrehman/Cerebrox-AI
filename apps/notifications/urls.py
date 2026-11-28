from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    path('api/summary/', views.notification_summary, name='summary'),
    path('api/read-all/', views.mark_all_read, name='read_all'),
    path('api/<int:notification_id>/read/', views.mark_notification_read, name='read'),
    path('<int:notification_id>/open/', views.open_notification, name='open'),
]
