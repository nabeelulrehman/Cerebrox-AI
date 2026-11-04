from django.contrib import admin
from .models import TeacherProfile


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'verification_status', 'subjects', 'experience_years', 'created_at')
    list_filter = ('verification_status', 'teaching_mode')
    search_fields = ('user__username', 'user__email', 'subjects')
    readonly_fields = ('verified_at', 'verified_by')
