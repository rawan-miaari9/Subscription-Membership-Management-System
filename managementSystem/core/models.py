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
        ("Staff", "Staff"),
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

    def get_full_name(self):
        return self.full_name or self.username

    @property
    def first_name(self):
        return (self.full_name or "").split()[0] if self.full_name else ""

    @property
    def last_name(self):
        parts = (self.full_name or "").split()
        return parts[-1] if len(parts) > 1 else ""

    @property
    def last_login(self):
        return self.updated_at


class Member(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('expiring', 'Expiring'),
        ('suspended', 'Suspended'),
        ('expired', 'Expired'),
    ]

    id = models.AutoField(primary_key=True)
    member_code = models.CharField(max_length=20, unique=True)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(max_length=254, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    join_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
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
    member = models.ForeignKey(Member, on_delete=models.DO_NOTHING, db_column="member_id", blank=True, null=True, related_name="subscriptions")
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


class Payment(models.Model):
    METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('card', 'Card'),
        ('bank_transfer', 'Bank Transfer'),
        ('online', 'Online'),
    ]
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('pending', 'Pending'),
        ('failed', 'Failed'),
    ]

    payment_code = models.CharField(max_length=50, null=True, blank=True)
    receipt_no = models.CharField(max_length=50, null=True, blank=True)
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        db_column='member_id',
        null=True,
        blank=True,
        related_name='payments',
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        db_column='subscription_id',
        null=True,
        blank=True,
        related_name='payments',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'payments'

    def __str__(self):
        return self.payment_code or f"Payment #{self.pk}"


class Attendance(models.Model):
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        db_column='member_id',
        null=True,
        blank=True,
        related_name='attendance_records',
    )
    date = models.DateField()
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    duration_min = models.IntegerField(null=True, blank=True, editable=False)

    class Meta:
        managed = False
        db_table = 'attendance'
        unique_together = ('member', 'date')

    def __str__(self):
        return f"{self.member} - {self.date}"


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

class Service(models.Model):
    service_code = models.CharField(max_length=50, unique=True, null=True, blank=True)
    name = models.CharField(max_length=150)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(null=True, blank=True, default=True)

    class Meta:
        managed = False
        db_table = 'services'

    def __str__(self):
        return self.name


class PlanService(models.Model):
    plan = models.ForeignKey(
        MembershipPlan,
        on_delete=models.CASCADE,
        db_column='plan_id',
        null=True,
        blank=True,
        related_name='plan_services',
    )
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        db_column='service_id',
        null=True,
        blank=True,
        related_name='plan_services',
    )

    class Meta:
        managed = False
        db_table = 'plan_services'
        unique_together = ('plan', 'service')

    def __str__(self):
        return f"{self.plan} — {self.service}"


class UserProfile(models.Model):
    ROLE_ADMIN = 'admin'
    ROLE_ACCOUNTANT = 'accountant'
    ROLE_STAFF = 'staff'
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Admin'),
        (ROLE_ACCOUNTANT, 'Accountant'),
        (ROLE_STAFF, 'Staff'),
    ]

    user = models.OneToOneField(
        'core.User',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_column='user_id',
        related_name='profile',
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, null=True, blank=True)
    avatar = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'user_profiles'

    def __str__(self):
        return f"{self.user} ({self.role})" if self.user_id else f"UserProfile #{self.pk}"


class Invoice(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('paid', 'Paid'),
        ('partial', 'Partial'),
        ('void', 'Void'),
    ]
    DISCOUNT_TYPE_CHOICES = [
        ('flat', 'Flat Amount'),
        ('percent', 'Percentage'),
    ]
    TERM_CHOICES = [
        ('due_on_receipt', 'Due on Receipt'),
        ('net_7', 'Net 7'),
        ('net_15', 'Net 15'),
        ('net_30', 'Net 30'),
        ('net_60', 'Net 60'),
        ('custom', 'Custom'),
    ]

    invoice_no = models.CharField(max_length=50, unique=True)
    bill_to = models.CharField(max_length=255, null=True, blank=True)
    member = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        db_column='member_id',
        null=True,
        blank=True,
        related_name='invoices',
    )
    # payments isn't modeled yet — plain FK id for now (nullable, unique per payments row).
    payment_id = models.IntegerField(null=True, blank=True)

    description = models.TextField(null=True, blank=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE_CHOICES, default='flat')
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    payment_terms = models.CharField(max_length=50, choices=TERM_CHOICES, default='due_on_receipt')
    notes = models.TextField(null=True, blank=True)
    issued_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'invoices'
        ordering = ['-issued_date', '-id']

    def save(self, *args, **kwargs):
        if self.created_at is None:
            self.created_at = timezone.now()
        if not self.due_date and self.issued_date:
            self.due_date = self.issued_date
        super().save(*args, **kwargs)

    @property
    def billed_to(self):
        """Who the invoice is issued to: explicit name, else linked member, else guest."""
        if self.bill_to and self.bill_to.strip():
            return self.bill_to.strip()
        if self.member:
            return self.member.full_name
        return "Walk-in / Guest"

    @property
    def discount_amount(self):
        """Actual money amount removed by the discount, based on the stored value."""
        zero = Decimal('0.00')
        subtotal = self.subtotal or zero
        if self.discount_type == 'percent':
            rate = (self.discount or zero) / Decimal('100')
            return (subtotal * rate).quantize(Decimal('0.01'))
        return min(self.discount or zero, subtotal)

    @property
    def balance(self):
        return (self.total or Decimal('0')) - (self.amount_paid or Decimal('0'))

    @property
    def effective_status(self):
        """Status used for display: surfaces overdue automatically once due."""
        if self.status in ('paid', 'void'):
            return self.status
        if self.balance > 0 and self.due_date and self.due_date < timezone.localdate():
            return 'overdue'
        if self.status == 'partial' or (self.amount_paid and self.balance > 0):
            return 'partial'
        return self.status

    def recalculate(self):
        """Compute tax and total from subtotal/discount/tax-rate; keep discount as entered."""
        zero = Decimal('0.00')
        discount_amount = self.discount_amount
        tax_base = max((self.subtotal or zero) - discount_amount, zero)
        self.tax_amount = (tax_base * (self.tax_rate or zero) / Decimal('100')).quantize(Decimal('0.01'))
        self.total = ((self.subtotal or zero) - discount_amount + self.tax_amount).quantize(Decimal('0.01'))
        return self

    def __str__(self):
        return self.invoice_no or f"Invoice #{self.pk}"


class Receipt(models.Model):
    METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('card', 'Card'),
        ('bank_transfer', 'Bank Transfer'),
        ('other', 'Other'),
    ]
    DISCOUNT_TYPE_CHOICES = Invoice.DISCOUNT_TYPE_CHOICES

    receipt_no = models.CharField(max_length=50, unique=True)
    bill_to = models.CharField(max_length=255, null=True, blank=True)
    member = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        db_column='member_id',
        null=True,
        blank=True,
        related_name='receipts',
    )
    invoice_id = models.IntegerField(null=True, blank=True)
    payment_id = models.IntegerField(null=True, blank=True)

    description = models.TextField(null=True, blank=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE_CHOICES, default='flat')
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='cash')
    notes = models.TextField(null=True, blank=True)
    paid_date = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(null=True, blank=True)
    logo = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'receipts'
        ordering = ['-paid_date', '-id']

    def save(self, *args, **kwargs):
        if self.created_at is None:
            self.created_at = timezone.now()
        super().save(*args, **kwargs)

    @property
    def received_from(self):
        """Who the receipt was issued to: explicit name, else linked member, else walk-in."""
        if self.bill_to and self.bill_to.strip():
            return self.bill_to.strip()
        if self.member:
            return self.member.full_name
        return "Walk-in / Guest"

    @property
    def discount_amount(self):
        zero = Decimal('0.00')
        subtotal = self.subtotal or zero
        if self.discount_type == 'percent':
            rate = (self.discount or zero) / Decimal('100')
            return (subtotal * rate).quantize(Decimal('0.01'))
        return min(self.discount or zero, subtotal)

    def recalculate(self):
        """Compute tax and total from subtotal/discount/tax-rate; keep discount as entered."""
        zero = Decimal('0.00')
        discount_amount = self.discount_amount
        tax_base = max((self.subtotal or zero) - discount_amount, zero)
        self.tax_amount = (tax_base * (self.tax_rate or zero) / Decimal('100')).quantize(Decimal('0.01'))
        self.total = ((self.subtotal or zero) - discount_amount + self.tax_amount).quantize(Decimal('0.01'))
        return self

    def __str__(self):
        return self.receipt_no or f"Receipt #{self.pk}"


class Financial(models.Model):
    """Global financial settings (single row) — maps to existing 'financial' table.

    The tax rate configured here is the fixed rate applied to invoices & receipts.
    """

    currency = models.CharField(max_length=50, null=True, blank=True)
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'financial'

    @classmethod
    def get_tax_rate(cls):
        """Return the persisted global tax rate, defaulting to 0.00."""
        try:
            obj = cls.objects.first()
            if obj and obj.tax_rate is not None:
                return obj.tax_rate
        except Exception:
            pass
        return Decimal('0.00')

    def __str__(self):
        return f"Financial (id={self.pk})"
