from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.core.validators import validate_email


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


class User(models.Model):
    """App user stored in the "users" table.

    The admin-profile Settings tab updates the seeded admin row (pk=1).
    Passwords are stored hashed via Django's hashers.
    """
    full_name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=60, unique=True)
    password = models.CharField(max_length=128)
    role = models.CharField(max_length=50, default="Admin")
    status = models.CharField(max_length=20, default="Active")
    avatar = models.ImageField(upload_to="settings/avatars/", blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "users"
        ordering = ["pk"]
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return self.full_name

    def clean(self):
        super().clean()
        if self.email:
            try:
                validate_email(self.email)
            except ValidationError:
                raise ValidationError({"email": "Enter a valid email address."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def set_password(self, raw_password):
        """Hash and store the given password."""
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        """Verify a raw password against the stored hash."""
        return check_password(raw_password, self.password)

    @classmethod
    def get_admin(cls):
        """Return the single admin-profile row, creating it if needed."""
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "full_name": "Admin",
                "email": "admin@ironcore.gym",
                "username": "admin",
                "password": make_password("admin123"),
                "role": "Admin",
                "status": "Active",
            },
        )
        cls.repair_invalid_email(obj)
        return obj

    @classmethod
    def repair_invalid_email(cls, obj):
        """Replace a malformed stored email with the seed value."""
        try:
            validate_email(obj.email)
        except (ValidationError, TypeError):
            obj.email = "admin@ironcore.gym"
            obj.save(update_fields=["email"])
        return obj
