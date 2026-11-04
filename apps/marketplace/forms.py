from django import forms
from apps.accounts.forms import StyledFormMixin
from apps.classrooms.models import Classroom
from .models import Resource, GuidanceRequest, TeachingSession, Review


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
