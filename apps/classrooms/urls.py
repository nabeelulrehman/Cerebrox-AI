from django.urls import path
from . import views as v

app_name = 'classrooms'

urlpatterns = [
    # teacher
    path('teach/', v.teacher_classes, name='teacher_classes'),
    path('teach/<int:class_id>/', v.teacher_detail, name='teacher_detail'),
    path('teach/<int:class_id>/edit/', v.teacher_edit, name='teacher_edit'),
    path('teach/<int:class_id>/new-code/', v.teacher_regenerate_code, name='teacher_regenerate_code'),
    path('teach/<int:class_id>/invite/', v.teacher_invite, name='teacher_invite'),
    path('teach/<int:class_id>/announce/', v.teacher_announce, name='teacher_announce'),
    path('teach/<int:class_id>/students/<int:student_id>/', v.teacher_student_progress, name='teacher_student_progress'),
    path('teach/enrollments/<int:enrollment_id>/decide/', v.teacher_decide, name='teacher_decide'),
    path('teach/enrollments/<int:enrollment_id>/remove/', v.teacher_remove, name='teacher_remove'),
    # student (optional)
    path('mine/', v.my_classes, name='my_classes'),
    path('join/', v.join_class, name='join'),
    path('invites/<int:enrollment_id>/respond/', v.respond_invite, name='respond_invite'),
    path('enrollments/<int:enrollment_id>/leave/', v.leave, name='leave'),
    path('enrollments/<int:enrollment_id>/share/', v.toggle_share, name='toggle_share'),
    path('<int:class_id>/', v.student_class, name='student_class'),
]
