from django.contrib import admin
from .models import Classroom, Enrollment, Announcement

admin.site.register(Classroom)
admin.site.register(Enrollment)
admin.site.register(Announcement)
