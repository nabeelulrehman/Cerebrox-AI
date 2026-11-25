from django import forms
from apps.accounts.forms import StyledFormMixin
from .models import Classroom, Announcement


class ClassroomForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Classroom
        fields = ('name', 'subject', 'description', 'price', 'enrollment_policy', 'listed_publicly')

    def clean_price(self):
        price = self.cleaned_data['price']
        if price < 0:
            raise forms.ValidationError('Class price cannot be negative.')
        return price

    def clean(self):
        cleaned = super().clean()
        if (self.instance.pk and self.instance.price == 0 and cleaned.get('price', 0) > 0
                and self.instance.enrollments.filter(status='active').exists()):
            self.add_error('price', 'This class already has active students. Create a separate paid class to avoid changing their access.')
        return cleaned


class InviteForm(StyledFormMixin, forms.Form):
    identifier = forms.CharField(max_length=254, label='Student username or email')


class JoinCodeForm(StyledFormMixin, forms.Form):
    code = forms.CharField(max_length=12, label='Join code')


class AnnouncementForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ('title', 'body')
