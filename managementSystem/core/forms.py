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
