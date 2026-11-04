from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction

from apps.accounts.forms import StyledFormMixin
from apps.accounts.models import User
from .models import TeacherProfile


class TeacherRegisterForm(StyledFormMixin, UserCreationForm):
    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=True)
    headline = forms.CharField(max_length=140, required=False)
    bio = forms.CharField(
        label='Short professional bio',
        widget=forms.Textarea,
        max_length=2000,
        required=False,
        help_text='Briefly introduce your teaching approach and experience.',
    )
    qualifications = forms.CharField(widget=forms.Textarea)
    subjects = forms.CharField(max_length=255, help_text='Comma-separated, e.g. "Physics, Mathematics"')
    experience_years = forms.IntegerField(min_value=0, max_value=60, initial=0)
    teaching_mode = forms.ChoiceField(choices=TeacherProfile.Mode.choices)

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'password1', 'password2')

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.TEACHER          # role is set server-side, never from POST data
        user.email = self.cleaned_data['email']
        user.study_level = User.StudyLevel.OTHER
        user.study_level_custom = 'Teacher'
        if commit:
            with transaction.atomic():
                user.save()
                TeacherProfile.objects.create(
                    user=user,
                    headline=self.cleaned_data.get('headline', ''),
                    bio=self.cleaned_data.get('bio', ''),
                    qualifications=self.cleaned_data['qualifications'],
                    subjects=self.cleaned_data['subjects'],
                    experience_years=self.cleaned_data['experience_years'],
                    teaching_mode=self.cleaned_data['teaching_mode'],
                )
        return user


class TeacherProfileForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = TeacherProfile
        # verification fields are deliberately NOT editable by the teacher
        fields = ('headline', 'bio', 'qualifications', 'subjects', 'experience_years',
                  'teaching_mode', 'availability', 'offers_free_guidance', 'hourly_rate')
