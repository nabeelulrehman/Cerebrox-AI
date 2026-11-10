from django.contrib import admin
from .models import (
    Resource, Payment, GuidanceRequest, TeachingSession, SessionBooking, SessionAttendance, Review,
)

for m in (Resource, Payment, GuidanceRequest, TeachingSession, SessionBooking, SessionAttendance, Review):
    admin.site.register(m)
