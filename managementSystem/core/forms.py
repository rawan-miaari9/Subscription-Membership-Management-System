import re

from django import forms

from .models import Member


class MemberForm(forms.Form):
    # 'expired' is a valid DB status but isn't offered here on purpose — a member
    # is never *created* as already-expired; that status is only reached later
    # once a subscription lapses. This matches the existing frontend dropdown,
    # which only offers Active/Expiring/Suspended.
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
        # Not used for a uniqueness exclusion right now — phone/email have no
        # UNIQUE constraint in the DB, and member_code (the only unique column)
        # isn't a form field at all. Kept for parity with the instance_user
        # pattern used elsewhere in this project, and so __init__ has a place to
        # widen the status choices below when editing an already-expired member.
        self.instance_member = instance_member
        super().__init__(*args, **kwargs)
        if instance_member is not None and instance_member.status == 'expired':
            # 'expired' is intentionally not offered when adding/editing a member
            # normally (see STATUS_CHOICES comment), but if we're editing a member
            # who's already expired, it must stay selectable — otherwise saving the
            # form without touching the dropdown would silently flip them to Active.
            self.fields['status'].choices = self.STATUS_CHOICES + [('expired', 'Expired')]

    # full_name/email "not just whitespace" / "valid if provided" are already handled
    # by CharField's default strip=True + required=True, and EmailField's built-in
    # format validation (which is skipped entirely when the field is empty and
    # required=False) — no need to duplicate that here.

    def clean_phone(self):
        phone = self.cleaned_data['phone']
        # Loose sanity check, not a strict international-format validator: digits
        # plus common separators (spaces, dashes, dots, parens, a leading +).
        if not re.match(r'^[0-9+\-().\s]{6,20}$', phone):
            raise forms.ValidationError('Enter a valid phone number.')
        return phone

    @staticmethod
    def generate_member_code():
        """Next sequential "MBR-<n>" code, based on the highest existing numeric suffix.

        member_code isn't a numeric PK, so there's no DB identity/sequence to lean
        on — this mirrors the "MBR-XXXX" convention already used in this dataset by
        scanning existing codes in Python (safer than a lexicographic DB sort, since
        e.g. "MBR-9" would sort after "MBR-10" as plain text). This is an app-level
        sequence, not an atomic DB one: two concurrent Add Member submissions could
        compute the same next number. The save step (SMM2-137) should retry on a
        UNIQUE constraint violation, the same way the Attendance check-in flow
        already retries on a concurrent race for that table.
        """
        pattern = re.compile(r'^MBR-(\d+)$')
        max_num = 0
        codes = Member.objects.filter(member_code__startswith='MBR-').values_list('member_code', flat=True)
        for code in codes:
            match = pattern.match(code)
            if match:
                max_num = max(max_num, int(match.group(1)))
        return f"MBR-{max_num + 1:04d}"
