import re
from decimal import Decimal

from django import forms
from django.utils.text import slugify

from .models import Member, MembershipPlan, Payment, Service, Subscription, User, UserProfile


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


class PlanForm(forms.Form):
    # 'Status' dropdown in the frontend (templates/plans/create.html) offers
    # exactly these two options — kept as a string here (not converted to a
    # boolean) so the save step (SMM2-150) does that mapping explicitly, same
    # pattern used for MemberForm.status elsewhere in this project.
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    name = forms.CharField(max_length=150, label='Plan Name')
    duration_days = forms.IntegerField(min_value=1, label='Duration (days)')
    price = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0, label='Price')
    # The current Create Plan template (templates/plans/create.html) has no
    # explicit "is this a fixed-duration plan?" toggle — the closest thing is
    # its Duration dropdown's "Custom" option, but that option has no real
    # numeric duration attached to it there, and "Custom" plans are actually
    # their own separate flow in this app (templates/plans/custom.html /
    # plan_custom_view — a different creation page entirely, out of scope for
    # this form per the task breakdown). Rather than guess at a day-count
    # bucketing scheme the frontend doesn't actually implement, I'm exposing
    # is_fixed as its own plain checkbox, defaulting to True (this form is for
    # the *standard* plan flow, where a fixed duration is the normal case).
    is_fixed = forms.BooleanField(required=False, initial=True, label='Fixed Duration')
    status = forms.ChoiceField(choices=STATUS_CHOICES, initial='active', label='Status')

    def __init__(self, *args, instance_plan=None, **kwargs):
        # Used below to exclude the plan's own current row from the
        # uniqueness check when editing (member_code-style instance pattern
        # used elsewhere in this project).
        self.instance_plan = instance_plan
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        qs = MembershipPlan.objects.filter(name__iexact=name)
        if self.instance_plan is not None:
            qs = qs.exclude(pk=self.instance_plan.pk)
        if qs.exists():
            raise forms.ValidationError('A plan with this name already exists.')
        return name

    @staticmethod
    def generate_slug(name, instance_plan=None):
        """Slugify `name` and disambiguate against existing plans with a -2, -3, ... suffix.

        slug isn't a form field — it's not user-facing on the Create Plan page
        at all — so it's derived here from the (already-validated-unique) name
        instead. A unique `name` doesn't guarantee a unique slug on its own
        (e.g. "Gold!" and "Gold" both slugify to "gold"), so this still checks
        for collisions and appends a numeric suffix the same way
        MemberForm.generate_member_code() disambiguates member_code.
        """
        base_slug = slugify(name)
        slug = base_slug
        suffix = 1

        def taken(candidate):
            qs = MembershipPlan.objects.filter(slug=candidate)
            if instance_plan is not None:
                qs = qs.exclude(pk=instance_plan.pk)
            return qs.exists()

        while taken(slug):
            suffix += 1
            slug = f"{base_slug}-{suffix}"
        return slug


class ServiceForm(forms.Form):
    name = forms.CharField(max_length=150, label='Service Name')
    description = forms.CharField(required=False, widget=forms.Textarea, label='Description')
    # The real `services.is_active` column is a genuine boolean (not a
    # status string like Member/Plan use elsewhere in this project), and
    # there's no pre-existing frontend convention to match here (no add-form
    # template existed before this task) — so it's represented directly as a
    # checkbox rather than an Active/Inactive dropdown needing translation.
    is_active = forms.BooleanField(required=False, initial=True, label='Active')

    def __init__(self, *args, instance_service=None, **kwargs):
        # Not used for any validation yet (services.name has no UNIQUE
        # constraint in the DB, unlike Member/Plan names, so there's nothing
        # to exclude-self from). Accepted now anyway so a future Edit Service
        # view can pass it in without changing this form's signature —
        # deferred building that view itself, since this task's scope is
        # List + Add only.
        self.instance_service = instance_service
        super().__init__(*args, **kwargs)

    @staticmethod
    def generate_service_code():
        """Next sequential "SRV-<n>" code, based on the highest existing numeric suffix.

        Same numeric-max approach as MemberForm.generate_member_code(): scan
        existing codes in Python rather than sort them as text in the DB
        (avoids "SRV-9" sorting after "SRV-10"), matching the SRV-NNN pattern
        already visible in this page's previous fake data (SRV-001, SRV-002).
        """
        pattern = re.compile(r'^SRV-(\d+)$')
        max_num = 0
        codes = Service.objects.filter(service_code__startswith='SRV-').values_list('service_code', flat=True)
        for code in codes:
            match = pattern.match(code)
            if match:
                max_num = max(max_num, int(match.group(1)))
        return f"SRV-{max_num + 1:03d}"


class PaymentForm(forms.Form):
    member_search = forms.CharField(max_length=150, label='Member', required=True)
    member_id = forms.IntegerField(widget=forms.HiddenInput, required=False)
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'), label='Amount')
    payment_model = forms.ChoiceField(choices=[('full','Full Payment'),('partial','Partial')], initial='full', label='Payment Model')
    method = forms.ChoiceField(choices=[('card','Card'),('cash','Cash'),('transfer','Bank Transfer'),('online','Online')], label='Method')
    status = forms.ChoiceField(choices=[('success','Paid'),('pending','Pending')], initial='success', label='Status')

    def clean_member_search(self):
        val = self.cleaned_data['member_search'].strip()
        if not val:
            raise forms.ValidationError('Member is required.')
        return val

    def clean(self):
        cleaned = super().clean()
        member_id = cleaned.get('member_id')
        member_search = cleaned.get('member_search', '').strip()
        member = None
        if member_id:
            try:
                member = Member.objects.get(pk=member_id)
            except Member.DoesNotExist:
                self.add_error('member_search', 'Selected member not found.')
                return cleaned
        elif member_search:
            # Try to find by code, name, phone, email
            from django.db.models import Q
            qs = Member.objects.filter(
                Q(member_code__iexact=member_search) |
                Q(full_name__icontains=member_search) |
                Q(phone__icontains=member_search) |
                Q(email__icontains=member_search)
            )
            member = qs.first()
            if not member:
                # Try split like "MBR-123 | Name"
                code = member_search.split('|')[0].strip().split()[0]
                try:
                    member = Member.objects.get(member_code__iexact=code)
                except Member.DoesNotExist:
                    self.add_error('member_search', 'Member not found. Use search and select.')
                    return cleaned
            cleaned['member_id'] = member.id
            cleaned['member_obj'] = member
        else:
            self.add_error('member_search', 'Member is required.')
        if member:
            cleaned['member_obj'] = member
            # Auto-attach latest subscription if exists for receipt linking
            try:
                sub = Subscription.objects.filter(member=member).order_by('-end_date').first()
                cleaned['subscription_obj'] = sub
            except Exception:
                cleaned['subscription_obj'] = None
        return cleaned

    @staticmethod
    def generate_payment_code():
        pattern = re.compile(r'^PAY-(\d+)$')
        max_num = 0
        codes = Payment.objects.filter(payment_code__startswith='PAY-').values_list('payment_code', flat=True)
        for code in codes:
            m = pattern.match(code)
            if m:
                max_num = max(max_num, int(m.group(1)))
        return f"PAY-{max_num + 1:04d}"

    @staticmethod
    def generate_receipt_no():
        pattern = re.compile(r'^RCPT-(\d+)$')
        max_num = 0
        codes = Payment.objects.filter(receipt_no__startswith='RCPT-').values_list('receipt_no', flat=True)
        for code in codes:
            m = pattern.match(code)
            if m:
                max_num = max(max_num, int(m.group(1)))
        # Keep PAY and RCPT in sync for simplicity
        return f"RCPT-{max_num + 1:04d}"
