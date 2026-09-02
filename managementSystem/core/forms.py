import re

from django import forms

from .models import Member, User, UserProfile


class MemberForm(forms.Form):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('expiring', 'Expiring'),
        ('suspended', 'Suspended'),
    ]

    full_name = forms.CharField(max_length=150, label='Full Name')
    phone = forms.CharField(max_length=30, label='Phone')
    email = forms.EmailField(required=False, label='Email')
    join_date = forms.DateField(label='Join Date', widget=forms.DateInput(attrs={'type': 'date'}))
    status = forms.ChoiceField(choices=STATUS_CHOICES, initial='active', label='Status')

    def __init__(self, *args, instance_member=None, **kwargs):
        self.instance_member = instance_member
        super().__init__(*args, **kwargs)
        if instance_member is not None and instance_member.status == 'expired':
            self.fields['status'].choices = self.STATUS_CHOICES + [('expired', 'Expired')]

    def clean_phone(self):
        phone = self.cleaned_data['phone']
        if not re.match(r'^[0-9+\-().\s]{6,20}$', phone):
            raise forms.ValidationError('Enter a valid phone number.')
        return phone

    @staticmethod
    def generate_member_code():
        pattern = re.compile(r'^MBR-(\d+)$')
        max_num = 0
        codes = Member.objects.filter(member_code__startswith='MBR-').values_list('member_code', flat=True)
        for code in codes:
            match = pattern.match(code)
            if match:
                max_num = max(max_num, int(match.group(1)))
        return f"MBR-{max_num + 1:04d}"


class UserAddForm(forms.Form):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    full_name = forms.CharField(max_length=150, label='Full Name')
    email = forms.EmailField(label='Email')
    username = forms.CharField(max_length=150, min_length=3, label='Username')
    password = forms.CharField(min_length=6, required=True, widget=forms.PasswordInput, label='Password')
    role = forms.ChoiceField(choices=UserProfile.ROLE_CHOICES, label='Role')
    status = forms.ChoiceField(choices=STATUS_CHOICES, label='Status')

    def __init__(self, *args, instance_user=None, **kwargs):
        self.instance_user = instance_user
        super().__init__(*args, **kwargs)
        if instance_user is not None:
            self.fields['password'].required = False

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if ' ' in username:
            raise forms.ValidationError('Username cannot contain spaces.')
        qs = User.objects.filter(username=username)
        if self.instance_user is not None:
            qs = qs.exclude(pk=self.instance_user.pk)
        if qs.exists():
            raise forms.ValidationError('This username is already taken.')
        return username

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        qs = User.objects.filter(email=email)
        if self.instance_user is not None:
            qs = qs.exclude(pk=self.instance_user.pk)
        if qs.exists():
            raise forms.ValidationError('This email is already in use.')
        return email
