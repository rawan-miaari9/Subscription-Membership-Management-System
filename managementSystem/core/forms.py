from django import forms
from django.contrib.auth.models import User

from .models import UserProfile


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
            # Editing: password is optional — only set it if a new one is provided.
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
