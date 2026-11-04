from django.contrib import admin
from .models import Resource, Payment, GuidanceRequest, TeachingSession, SessionBooking, Review

for m in (Resource, Payment, GuidanceRequest, TeachingSession, SessionBooking, Review):
    admin.site.register(m)
