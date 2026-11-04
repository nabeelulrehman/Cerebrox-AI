from django import forms
from apps.accounts.forms import StyledFormMixin
from .models import Classroom, Announcement


class ClassroomForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Classroom
        fields = ('name', 'subject', 'description', 'enrollment_policy', 'listed_publicly')


class InviteForm(StyledFormMixin, forms.Form):
    identifier = forms.CharField(max_length=254, label='Student username or email')


class JoinCodeForm(StyledFormMixin, forms.Form):
    code = forms.CharField(max_length=12, label='Join code')


class AnnouncementForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ('title', 'body')
