from datetime import datetime

from django import forms
from django.utils import timezone
from apps.accounts.forms import StyledFormMixin
from .models import ClassQuiz, ClassQuestion


class ClassQuizForm(StyledFormMixin, forms.ModelForm):
    start_at = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
                                   input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'])
    end_at = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
                                 input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'])

    class Meta:
        model = ClassQuiz
        fields = ('title', 'description', 'start_at', 'end_at', 'duration_minutes', 'max_attempts',
                  'shuffle_questions', 'negative_marking', 'supervised', 'result_visibility')

    def clean(self):
        c = super().clean()
        if c.get('start_at') and c.get('end_at') and c['end_at'] <= c['start_at']:
            self.add_error('end_at', 'The end time must be after the start time.')
        if c.get('negative_marking') is not None and not (0 <= c['negative_marking'] <= 1):
            self.add_error('negative_marking', 'Use a fraction between 0 and 1.')
        return c


class QuizScheduleForm(StyledFormMixin, forms.Form):
    opening_date = forms.DateField(
        required=False,
        label='Opening date',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    opening_time = forms.TimeField(
        required=False,
        label='Opening time',
        widget=forms.TimeInput(attrs={'type': 'time'}),
    )
    closing_date = forms.DateField(
        required=False,
        label='Closing date',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    closing_time = forms.TimeField(
        required=False,
        label='Closing time',
        widget=forms.TimeInput(attrs={'type': 'time'}),
    )
    duration_minutes = forms.IntegerField(min_value=1, label='Duration (minutes)')
    confirm_attempts = forms.BooleanField(required=False)

    def __init__(self, *args, instance, **kwargs):
        self.instance = instance
        initial = kwargs.pop('initial', {})
        for prefix, value in (
            ('opening', instance.start_at),
            ('closing', instance.end_at),
        ):
            if value is not None:
                local_value = timezone.localtime(value)
                initial[f'{prefix}_date'] = local_value.date()
                initial[f'{prefix}_time'] = local_value.time().replace(tzinfo=None)
        initial['duration_minutes'] = instance.duration_minutes
        super().__init__(*args, initial=initial, **kwargs)

    def clean(self):
        cleaned = super().clean()
        for prefix, label in (('opening', 'Opening'), ('closing', 'Closing')):
            date_value = cleaned.get(f'{prefix}_date')
            time_value = cleaned.get(f'{prefix}_time')
            if bool(date_value) != bool(time_value):
                self.add_error(
                    f'{prefix}_time' if date_value else f'{prefix}_date',
                    f'Enter both the {label.lower()} date and time, or leave both blank.',
                )
                continue
            if date_value and time_value:
                local_datetime = datetime.combine(date_value, time_value)
                cleaned[f'{prefix}_at'] = timezone.make_aware(
                    local_datetime,
                    timezone.get_current_timezone(),
                )
            else:
                cleaned[f'{prefix}_at'] = None

        opening = cleaned.get('opening_at')
        closing = cleaned.get('closing_at')
        if opening and closing and closing <= opening:
            self.add_error('closing_time', 'Closing time must be later than opening time.')
        return cleaned

    def save(self):
        self.instance.start_at = self.cleaned_data['opening_at']
        self.instance.end_at = self.cleaned_data['closing_at']
        self.instance.duration_minutes = self.cleaned_data['duration_minutes']
        self.instance.save(update_fields=['start_at', 'end_at', 'duration_minutes'])
        return self.instance


class ClassQuestionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ClassQuestion
        fields = ('kind', 'text', 'option_a', 'option_b', 'option_c', 'option_d',
                  'correct_answer', 'model_answer', 'marks', 'explanation')

    def clean(self):
        c = super().clean()
        if c.get('kind') == ClassQuestion.Kind.MCQ:
            if not all(c.get(f'option_{k}') for k in 'abcd'):
                raise forms.ValidationError('Multiple-choice questions need all four options.')
            if (c.get('correct_answer') or '').upper() not in ('A', 'B', 'C', 'D'):
                self.add_error('correct_answer', 'Choose A, B, C or D.')
            else:
                c['correct_answer'] = c['correct_answer'].upper()
        else:
            c['correct_answer'] = ''
        if c.get('marks') is not None and c['marks'] <= 0:
            self.add_error('marks', 'Marks must be positive.')
        return c


class AIGenerateForm(StyledFormMixin, forms.Form):
    topic = forms.CharField(max_length=150)
    difficulty = forms.ChoiceField(choices=[('Easy', 'Easy'), ('Medium', 'Medium'), ('Hard', 'Hard')], initial='Medium')
    count = forms.IntegerField(min_value=1, max_value=20, initial=5)
    level = forms.CharField(max_length=60, initial='Undergraduate')
