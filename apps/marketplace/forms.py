from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from apps.accounts.forms import StyledFormMixin
from apps.classrooms.models import Classroom, Enrollment
from apps.teachers.models import TeacherProfile
from .models import Resource, GuidanceRequest, TeachingSession, Review

User = get_user_model()


class ResourceForm(StyledFormMixin, forms.ModelForm):
    classrooms = forms.ModelMultipleChoiceField(queryset=Classroom.objects.none(), required=False,
                                                widget=forms.CheckboxSelectMultiple, label='Share with classes')

    class Meta:
        model = Resource
        fields = ('title', 'description', 'file', 'visibility', 'price', 'classrooms')

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
        f = c.get('file')
        if f:
            from .services import validate_upload, MarketError
            try:
                validate_upload(f)
            except MarketError as exc:
                self.add_error('file', str(exc))
        return c


class GuidanceForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = GuidanceRequest
        fields = ('message',)


class GuidanceComposeForm(StyledFormMixin, forms.Form):
    recipient = forms.ModelChoiceField(queryset=User.objects.none(), label='To')
    subject = forms.CharField(max_length=160)
    message = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Write your message…'}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if user.is_student:
            self.fields['recipient'].queryset = User.objects.filter(
                teacher_profile__verification_status=TeacherProfile.Status.APPROVED,
            ).order_by('first_name', 'username')
        elif user.is_verified_teacher:
            active_student_ids = Enrollment.objects.filter(
                classroom__teacher=user,
                status=Enrollment.Status.ACTIVE,
            ).values_list('student_id', flat=True)
            existing_student_ids = GuidanceRequest.objects.filter(
                teacher=user,
            ).values_list('student_id', flat=True)
            self.fields['recipient'].queryset = User.objects.filter(
                role=User.Role.STUDENT,
            ).filter(
                Q(pk__in=active_student_ids) | Q(pk__in=existing_student_ids),
            ).distinct().order_by('first_name', 'username')


class GuidanceReplyForm(StyledFormMixin, forms.Form):
    message = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Write a reply…'}),
    )


class SessionForm(StyledFormMixin, forms.ModelForm):
    starts_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
                                    input_formats=['%Y-%m-%dT%H:%M'])

    class Meta:
        model = TeachingSession
        fields = ('title', 'description', 'starts_at', 'duration_minutes', 'price', 'capacity', 'meeting_link')


class ReviewForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Review
        fields = ('rating', 'comment')

    def clean_rating(self):
        r = self.cleaned_data['rating']
        if not 1 <= r <= 5:
            raise forms.ValidationError('Rating must be 1 to 5.')
        return r
