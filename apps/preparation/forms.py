from django import forms
from django.utils import timezone
from apps.accounts.forms import StyledFormMixin
from .models import PreparationPlan, PreparationTopic


class PreparationPlanForm(StyledFormMixin, forms.ModelForm):
    syllabus_file = forms.FileField(required=False, help_text='PDF, DOC, DOCX, PPT, PPTX, or image; up to 10 MB.')
    study_days = forms.MultipleChoiceField(choices=[(str(i), day) for i, day in enumerate(
        ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'])],
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'study-days-list'}), required=False)
    timezone = forms.ChoiceField(choices=[('Asia/Karachi', 'Pakistan (Asia/Karachi)'), ('UTC', 'UTC'),
        ('Asia/Dubai', 'Gulf (Asia/Dubai)'), ('Europe/London', 'UK (Europe/London)'),
        ('America/New_York', 'Eastern US (America/New_York)')], initial='Asia/Karachi')

    class Meta:
        model = PreparationPlan
        fields = ('exam_name', 'exam_type', 'deadline', 'syllabus_text', 'learning_level', 'goals',
                  'study_hours_per_week', 'study_days', 'preferred_time', 'session_minutes', 'email_reminders', 'timezone')
        widgets = {'deadline': forms.DateInput(attrs={'type': 'date'}),
                   'preferred_time': forms.TimeInput(attrs={'type': 'time'}),
                   'syllabus_text': forms.Textarea(attrs={'rows': 5}),
                   'goals': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial['study_days'] = self.instance.study_days
            self.initial['timezone'] = self.instance.timezone

    def clean_deadline(self):
        deadline = self.cleaned_data['deadline']
        if deadline < timezone.localdate():
            raise forms.ValidationError('Choose today or a future date.')
        return deadline

    def clean_study_hours_per_week(self):
        value = self.cleaned_data['study_hours_per_week']
        if not 1 <= value <= 60:
            raise forms.ValidationError('Choose between 1 and 60 hours per week.')
        return value

    def clean_session_minutes(self):
        value = self.cleaned_data['session_minutes']
        if not 15 <= value <= 240:
            raise forms.ValidationError('Choose a session duration from 15 to 240 minutes.')
        return value

    def clean_syllabus_file(self):
        file = self.cleaned_data.get('syllabus_file')
        if not file:
            return file
        allowed = {'.pdf', '.doc', '.docx', '.ppt', '.pptx', '.png', '.jpg', '.jpeg', '.webp'}
        import os
        if os.path.splitext(file.name)[1].lower() not in allowed or file.size > 10 * 1024 * 1024:
            raise forms.ValidationError('Use a PDF, DOC/DOCX, PPT/PPTX, or image no larger than 10 MB.')
        return file

    def clean(self):
        data = super().clean()
        if not data.get('syllabus_text') and not data.get('syllabus_file'):
            self.add_error('syllabus_text', 'Enter syllabus topics or upload a syllabus file.')
        return data


class AssessmentForm(StyledFormMixin, forms.Form):
    topic = forms.ModelChoiceField(queryset=PreparationTopic.objects.none(), required=False)
    kind = forms.ChoiceField(choices=[('quiz', 'Practice quiz'), ('mock', 'Mock examination')])
    score = forms.DecimalField(min_value=0, max_digits=5, decimal_places=2)
    possible = forms.DecimalField(min_value=0.01, max_digits=5, decimal_places=2)

    def __init__(self, *args, plan, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['topic'].queryset = plan.topics.all()

    def clean(self):
        data = super().clean()
        if data.get('score') is not None and data.get('possible') is not None and data['score'] > data['possible']:
            self.add_error('score', 'Score cannot exceed the possible marks.')
        return data
