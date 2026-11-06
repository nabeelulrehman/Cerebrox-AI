from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from .models import User


class StyledFormMixin:
    """Applies the project's `.field` CSS class to every widget automatically,
    so templates can just do `{{ form.some_field }}` without repeating attrs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            existing = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (existing + ' field').strip()
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault('rows', 3)


class RegisterForm(StyledFormMixin, UserCreationForm):
    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=True)
    study_level = forms.ChoiceField(choices=User.StudyLevel.choices, initial=User.StudyLevel.UNDERGRADUATE)
    study_level_custom = forms.CharField(
        max_length=60, required=False,
        help_text="Only needed if you selected 'Other' above.",
    )

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email',
                   'study_level', 'study_level_custom', 'password1', 'password2')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('study_level') == User.StudyLevel.OTHER and not cleaned.get('study_level_custom'):
            self.add_error('study_level_custom', "Please describe your study level.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.STUDENT
        user.email = self.cleaned_data['email']
        user.study_level = self.cleaned_data['study_level']
        user.study_level_custom = self.cleaned_data.get('study_level_custom', '')
        if commit:
            user.save()
        return user


class LoginForm(StyledFormMixin, AuthenticationForm):
    username = forms.CharField(label='Username or email')

    def clean(self):
        identifier = self.cleaned_data.get('username')
        if identifier:
            identifier = identifier.strip()
            self.cleaned_data['username'] = identifier
            if not User.objects.filter(username=identifier).exists():
                email_user = User.objects.filter(email__iexact=identifier).first()
                if email_user:
                    self.cleaned_data['username'] = email_user.get_username()
        return super().clean()


class ProfileForm(StyledFormMixin, forms.ModelForm):
    MAX_AVATAR_SIZE = 5 * 1024 * 1024
    ALLOWED_AVATAR_FORMATS = {'JPEG', 'PNG', 'WEBP'}
    avatar = forms.ImageField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            'accept': 'image/jpeg,image/png,image/webp',
            'data-profile-avatar-input': '',
        }),
    )

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'bio', 'avatar', 'study_level', 'study_level_custom')

    def clean_avatar(self):
        avatar = self.cleaned_data.get('avatar')
        uploaded_file = self.files.get(self.add_prefix('avatar'))
        if not uploaded_file:
            return avatar

        if uploaded_file.size > self.MAX_AVATAR_SIZE:
            raise forms.ValidationError('Profile images must be 5 MB or smaller.')

        image_format = getattr(getattr(avatar, 'image', None), 'format', None)
        if image_format not in self.ALLOWED_AVATAR_FORMATS:
            raise forms.ValidationError('Upload a JPEG, PNG, or WebP image.')

        return avatar

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('study_level') == User.StudyLevel.OTHER and not cleaned.get('study_level_custom'):
            self.add_error('study_level_custom', "Please describe your study level.")
        return cleaned


class AdminPasswordChangeForm(StyledFormMixin, forms.Form):
    old_password = forms.CharField(widget=forms.PasswordInput)
    new_password1 = forms.CharField(widget=forms.PasswordInput, min_length=8)
    new_password2 = forms.CharField(widget=forms.PasswordInput, min_length=8)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        if not self.user.check_password(self.cleaned_data['old_password']):
            raise forms.ValidationError('Your current password is incorrect.')
        return self.cleaned_data['old_password']

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('new_password1'), cleaned.get('new_password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("The two new passwords don't match.")
        return cleaned

    def save(self):
        self.user.set_password(self.cleaned_data['new_password1'])
        self.user.save()
        return self.user
