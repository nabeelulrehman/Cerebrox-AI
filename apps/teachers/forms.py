from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction

from apps.accounts.forms import StyledFormMixin
from apps.accounts.models import User
from .models import TeacherProfile, TeacherPaymentMethod


class TeacherRegisterForm(StyledFormMixin, UserCreationForm):
    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=True)
    bio = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}), label='Short professional bio')
    qualifications = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}))
    subjects = forms.CharField(max_length=255, help_text='Comma-separated, e.g. "Physics, Mathematics"')
    experience_years = forms.IntegerField(min_value=0, max_value=60, initial=0)
    teaching_mode = forms.ChoiceField(choices=TeacherProfile.Mode.choices)
    jazzcash_account = forms.CharField(max_length=100, required=False, label='JazzCash account number')
    easypaisa_account = forms.CharField(max_length=100, required=False, label='Easypaisa account number')
    payment_method = forms.ChoiceField(choices=TeacherPaymentMethod.Method.choices, required=False)
    account_title = forms.CharField(max_length=160, required=False, label='Account Holder Name')
    account_number = forms.CharField(max_length=100, required=False, label='Mobile Number / Account Number')
    iban = forms.CharField(max_length=64, required=False, label='IBAN')
    bank_name = forms.CharField(max_length=160, required=False, label='Bank Name')

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'password1', 'password2')

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get('payment_method')
        if method:
            if not cleaned.get('account_title'):
                self.add_error('account_title', 'Enter the account holder name.')
            if method in (TeacherPaymentMethod.Method.JAZZCASH, TeacherPaymentMethod.Method.EASYPAISA):
                if not cleaned.get('account_number'):
                    self.add_error('account_number', 'Enter the mobile number.')
            else:
                if not cleaned.get('bank_name'):
                    self.add_error('bank_name', 'Enter the bank name.')
                if not cleaned.get('account_number'):
                    self.add_error('account_number', 'Enter the account number.')
                if not cleaned.get('iban'):
                    self.add_error('iban', 'Enter the IBAN.')
        return cleaned

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
                    bio=self.cleaned_data.get('bio', ''),
                    qualifications=self.cleaned_data['qualifications'],
                    subjects=self.cleaned_data['subjects'],
                    experience_years=self.cleaned_data['experience_years'],
                    teaching_mode=self.cleaned_data['teaching_mode'],
                )
                account_title = user.get_full_name().strip() or user.username
                for method, field_name in (
                    (TeacherPaymentMethod.Method.JAZZCASH, 'jazzcash_account'),
                    (TeacherPaymentMethod.Method.EASYPAISA, 'easypaisa_account'),
                ):
                    account_number = self.cleaned_data.get(field_name, '').strip()
                    if account_number and self.cleaned_data.get('payment_method') != method:
                        TeacherPaymentMethod.objects.create(
                            teacher=user, method=method, account_title=account_title,
                            account_number=account_number,
                        )
                if self.cleaned_data.get('payment_method'):
                    TeacherPaymentMethod.objects.create(
                        teacher=user, method=self.cleaned_data['payment_method'],
                        account_title=self.cleaned_data['account_title'],
                        account_number=self.cleaned_data.get('account_number', ''),
                        iban=self.cleaned_data.get('iban', ''),
                        bank_name=self.cleaned_data.get('bank_name', ''),
                    )
        return user


class TeacherProfileForm(StyledFormMixin, forms.ModelForm):
    qualifications = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}))
    payment_method = forms.ChoiceField(choices=TeacherPaymentMethod.Method.choices, required=False, label='Payment Method')
    account_title = forms.CharField(max_length=160, required=False, label='Account Holder Name')
    account_number = forms.CharField(max_length=100, required=False, label='Mobile Number / Account Number')
    bank_name = forms.CharField(max_length=160, required=False, label='Bank Name')
    iban = forms.CharField(max_length=64, required=False, label='IBAN')

    def __init__(self, *args, payment_method=None, **kwargs):
        self.payment_method_instance = payment_method
        super().__init__(*args, **kwargs)
        if payment_method:
            for field, value in {
                'payment_method': payment_method.method,
                'account_title': payment_method.account_title,
                'account_number': payment_method.account_number,
                'bank_name': payment_method.bank_name,
                'iban': payment_method.iban,
            }.items():
                self.fields[field].initial = value

    def clean(self):
        data = super().clean()
        method = data.get('payment_method')
        if method:
            if not data.get('account_title'):
                self.add_error('account_title', 'Enter the account holder name.')
            if method in (TeacherPaymentMethod.Method.JAZZCASH, TeacherPaymentMethod.Method.EASYPAISA):
                if not data.get('account_number'):
                    self.add_error('account_number', 'Enter the mobile number.')
            else:
                for field, message in (('bank_name', 'Enter the bank name.'),
                                       ('account_number', 'Enter the account number.'),
                                       ('iban', 'Enter the IBAN.')):
                    if not data.get(field):
                        self.add_error(field, message)
        return data

    def save_payment_method(self, teacher):
        method = self.cleaned_data.get('payment_method')
        if not method:
            TeacherPaymentMethod.objects.filter(teacher=teacher, is_active=True).update(is_active=False)
            return
        item = self.payment_method_instance or TeacherPaymentMethod(teacher=teacher)
        item.method = method
        item.account_title = self.cleaned_data['account_title']
        item.account_number = self.cleaned_data.get('account_number', '')
        item.bank_name = self.cleaned_data.get('bank_name', '')
        item.iban = self.cleaned_data.get('iban', '')
        item.is_active = True
        item.save()
        TeacherPaymentMethod.objects.filter(teacher=teacher, is_active=True).exclude(pk=item.pk).update(is_active=False)

    class Meta:
        model = TeacherProfile
        # verification fields are deliberately NOT editable by the teacher
        fields = ('qualifications', 'subjects', 'experience_years',
                  'teaching_mode', 'availability', 'offers_free_guidance')
