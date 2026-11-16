from datetime import datetime

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from apps.accounts.forms import StyledFormMixin
from apps.classrooms.models import Classroom, Enrollment
from apps.teachers.models import TeacherProfile
from .models import Resource, GuidanceRequest, TeachingSession, Review

User = get_user_model()


class ResourceMetadataForm(StyledFormMixin, forms.ModelForm):
    classrooms = forms.ModelMultipleChoiceField(queryset=Classroom.objects.none(), required=False,
                                                widget=forms.CheckboxSelectMultiple, label='Share with classes')

    class Meta:
        model = Resource
        fields = ('title', 'description', 'visibility', 'price', 'classrooms')

    def __init__(self, *args, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['classrooms'].queryset = Classroom.objects.filter(teacher=teacher)
        self.fields['classrooms'].widget.attrs['class'] = ''

    def clean(self):
        c = super().clean()
        vis = c.get('visibility')
        if vis == Resource.Visibility.PAID and not (c.get('price') and c['price'] > 0):
            self.add_error('price', 'Paid resources need a price above zero.')
        if vis == Resource.Visibility.CLASSES and not c.get('classrooms'):
            self.add_error('classrooms', 'Pick at least one of your classes.')
        return c


class ResourceForm(ResourceMetadataForm):
    """Legacy local form upload, retained for development and non-Vercel hosts."""
    class Meta(ResourceMetadataForm.Meta):
        fields = ('title', 'description', 'file', 'visibility', 'price', 'classrooms')

    def clean(self):
        cleaned = super().clean()
        uploaded_file = cleaned.get('file')
        if uploaded_file:
            from .services import validate_upload, MarketError
            try:
                validate_upload(uploaded_file)
            except MarketError as exc:
                self.add_error('file', str(exc))
        return cleaned


class GuidanceForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = GuidanceRequest
        fields = ('message',)


class GuidanceComposeForm(StyledFormMixin, forms.Form):
    recipient = forms.ModelChoiceField(queryset=User.objects.none(), label='To', widget=forms.HiddenInput)
    subject = forms.CharField(max_length=160)
    message = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Write your message…'}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['recipient'].queryset = self.recipient_queryset(user)

    @staticmethod
    def recipient_queryset(user):
        if user.is_student:
            queryset = User.objects.filter(
                teacher_profile__verification_status=TeacherProfile.Status.APPROVED,
            )
        elif user.is_verified_teacher:
            active_student_ids = Enrollment.objects.filter(
                classroom__teacher=user,
                status=Enrollment.Status.ACTIVE,
            ).values_list('student_id', flat=True)
            existing_student_ids = GuidanceRequest.objects.filter(
                teacher=user,
            ).values_list('student_id', flat=True)
            queryset = User.objects.filter(
                role=User.Role.STUDENT,
            ).filter(
                Q(pk__in=active_student_ids) | Q(pk__in=existing_student_ids),
            ).distinct()
        else:
            queryset = User.objects.none()
        return queryset.exclude(pk=user.pk).order_by('first_name', 'last_name', 'username')


class GuidanceReplyForm(StyledFormMixin, forms.Form):
    message = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Write a reply…'}),
    )


class SessionForm(StyledFormMixin, forms.ModelForm):
    scheduled_date = forms.DateField(
        label='Scheduled date',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    start_time = forms.TimeField(
        label='Start time',
        widget=forms.TimeInput(attrs={'type': 'time'}),
    )
    end_time = forms.TimeField(
        label='End time',
        widget=forms.TimeInput(attrs={'type': 'time'}),
    )

    class Meta:
        model = TeachingSession
        fields = (
            'title', 'subject', 'description', 'price', 'capacity', 'instructions',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            local_start = timezone.localtime(self.instance.starts_at)
            local_end = timezone.localtime(self.instance.ends_at)
            self.fields['scheduled_date'].initial = local_start.date()
            self.fields['start_time'].initial = local_start.time().replace(tzinfo=None)
            self.fields['end_time'].initial = local_end.time().replace(tzinfo=None)
        self.fields['capacity'].label = 'Maximum students'
        self.fields['capacity'].min_value = 1

    def clean(self):
        cleaned_data = super().clean()
        scheduled_date = cleaned_data.get('scheduled_date')
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')
        if scheduled_date and start_time and end_time:
            start = datetime.combine(scheduled_date, start_time)
            end = datetime.combine(scheduled_date, end_time)
            if end <= start:
                self.add_error('end_time', 'End time must be after the start time on the same date.')
            else:
                aware_start = timezone.make_aware(start, timezone.get_current_timezone())
                aware_end = timezone.make_aware(end, timezone.get_current_timezone())
                if aware_start <= timezone.now():
                    self.add_error('start_time', 'Choose a future start time.')
                self.cleaned_start = aware_start
                self.cleaned_duration = int((aware_end - aware_start).total_seconds() // 60)
        capacity = cleaned_data.get('capacity')
        if capacity is not None and self.instance.pk:
            active_bookings = self.instance.bookings.exclude(status='cancelled').count()
            if capacity < active_bookings:
                self.add_error('capacity', 'Maximum students cannot be lower than the current bookings.')
        return cleaned_data

    def save(self, commit=True):
        session = super().save(commit=False)
        session.starts_at = self.cleaned_start
        session.duration_minutes = self.cleaned_duration
        if commit:
            session.save()
            self.save_m2m()
        return session


class ReviewForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Review
        fields = ('rating', 'comment')

    def clean_rating(self):
        r = self.cleaned_data['rating']
        if not 1 <= r <= 5:
            raise forms.ValidationError('Rating must be 1 to 5.')
        return r
