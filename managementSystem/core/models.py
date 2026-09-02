from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.core.validators import validate_email
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone


class User(models.Model):
    ROLE_CHOICES = [
        ("Admin", "Admin"),
        ("Accountant", "Accountant"),
    ]
    STATUS_CHOICES = [
        ("Active", "Active"),
        ("Inactive", "Inactive"),
        ("Suspended", "Suspended"),
    ]

    id = models.BigAutoField(primary_key=True)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(max_length=254, unique=True)
    username = models.CharField(max_length=60, unique=True)
    password = models.CharField(max_length=128)
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, default="Admin")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Active")
    avatar = models.CharField(max_length=100, blank=True, null=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "users"
        managed = False

    def __str__(self):
        return f"{self.username} ({self.role})"

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    @property
    def is_active(self):
        return self.status == "Active"

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False


class Member(models.Model):
    id = models.AutoField(primary_key=True)
    member_code = models.CharField(max_length=20, unique=True)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(max_length=254, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    join_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=10, default="active")
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    initials = models.CharField(max_length=5, blank=True, null=True)

    class Meta:
        db_table = "members"
        managed = False

    def __str__(self):
        return f"{self.full_name} ({self.member_code})"


class MembershipPlan(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    slug = models.CharField(max_length=100, blank=True, null=True)
    duration_days = models.IntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_fixed = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "membership_plans"
        managed = False

    def __str__(self):
        return f"{self.name} - ${self.price}"


class Subscription(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("expiring", "Expiring"),
        ("expired", "Expired"),
        ("suspended", "Suspended"),
        ("cancelled", "Cancelled"),
    ]
    id = models.AutoField(primary_key=True)
    subscription_code = models.CharField(max_length=20, blank=True, null=True, unique=True)
    member = models.ForeignKey(Member, on_delete=models.DO_NOTHING, db_column="member_id", blank=True, null=True)
    plan = models.ForeignKey(MembershipPlan, on_delete=models.DO_NOTHING, db_column="plan_id", blank=True, null=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=10, default="active")
    auto_renew = models.BooleanField(default=True)
    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "subscriptions"
        managed = False

    def __str__(self):
        return f"{self.subscription_code} - {self.member} - {self.status}"


class FinancialSetting(models.Model):
    """Settings for the Financial tab, stored in a single row.

    The table is explicitly named "financial" per the settings spec.
    Currency is restricted to USD.
    """
    currency = models.CharField(max_length=3, default="USD")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=14)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "financial"
        verbose_name = "Financial Setting"
        verbose_name_plural = "Financial Settings"

    def __str__(self):
        return f"{self.currency} {self.tax_rate}%"

    @classmethod
    def get_singleton(cls):
        """Return the single financial row, creating it if needed."""
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={"currency": "USD", "tax_rate": 14},
        )
        return obj


class BusinessInformation(models.Model):
    """Settings for the Business Information tab.

    Stored as a single row containing the facility's identity details. The
    image fields are optional and served via Django's media storage.
    """
    business_name = models.CharField(max_length=200)
    logo = models.ImageField(upload_to="settings/logos/", blank=True, null=True)
    phone = models.CharField(max_length=50)
    email = models.EmailField()
    address = models.CharField(max_length=255)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Business Information"
        verbose_name_plural = "Business Information"

    def __str__(self):
        return self.business_name

    @classmethod
    def get_singleton(cls):
        """Return the single business-information row, creating it if needed."""
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "business_name": "IRON CORE STRENGTH",
                "phone": "+1 (555) 019-8372",
                "email": "admin@ironcore.gym",
                "address": "400 Industrial Blvd, Metropolis, 90210",
            },
        )
        return obj


class PaymentMethod(models.Model):
    """Settings for the Payment Methods tab.

    One row per method (cash, card, transfer, online). The code is a stable
    identifier shared with the frontend so checkboxes map back to rows.
    """
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Payment Method"
        verbose_name_plural = "Payment Methods"
        ordering = ["pk"]

    def __str__(self):
        return self.name

    @classmethod
    def get_all(cls):
        """Return all payment-method rows in display order, seeding defaults as needed."""
        defaults = {
            "cash": ("Cash", True),
            "card": ("Card", True),
            "transfer": ("Bank Transfer", True),
            "online": ("Online", False),
        }
        rows = {m.code: m for m in cls.objects.all()}
        for code, (name, enabled) in defaults.items():
            if code not in rows:
                rows[code] = cls.objects.create(code=code, name=name, enabled=enabled)
        return [rows[code] for code in defaults]


class NotificationSetting(models.Model):
    """Settings for the Notifications tab.

    One row per notification type (expiration, renewal). The code is a stable
    identifier shared with the frontend so toggles map back to rows.
    """
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Notification Setting"
        verbose_name_plural = "Notification Settings"
        ordering = ["pk"]

    def __str__(self):
        return self.name

    @classmethod
    def get_all(cls):
        """Return all notification rows in display order, seeding defaults as needed."""
        defaults = {
            "expiration": ("Expiration Alerts", "Notify members & staff when subscriptions near expiry.", True),
            "renewal": ("Renewal Reminders", "Automated reminders for renewals and failed payments.", True),
        }
        rows = {n.code: n for n in cls.objects.all()}
        for code, (name, description, enabled) in defaults.items():
            if code not in rows:
                rows[code] = cls.objects.create(code=code, name=name, description=description, enabled=enabled)
        return [rows[code] for code in defaults]
