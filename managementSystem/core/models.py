from decimal import Decimal

from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Member(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('expiring', 'Expiring'),
        ('suspended', 'Suspended'),
        ('expired', 'Expired'),
    ]

    member_code = models.CharField(max_length=50, unique=True)
    full_name = models.CharField(max_length=150)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=30, null=True, blank=True)
    join_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, null=True, blank=True)
    balance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    initials = models.CharField(max_length=10, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'members'

    def __str__(self):
        return self.full_name


class Subscription(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('expiring', 'Expiring'),
        ('expired', 'Expired'),
        ('suspended', 'Suspended'),
        ('cancelled', 'Cancelled'),
    ]

    subscription_code = models.CharField(max_length=50, null=True, blank=True)
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        db_column='member_id',
        null=True,
        blank=True,
        related_name='subscriptions',
    )
    # membership_plans isn't modeled yet (out of scope for this task) — plain FK id for now.
    plan_id = models.IntegerField(null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, null=True, blank=True)
    auto_renew = models.BooleanField(null=True, blank=True, default=True)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'subscriptions'

    def __str__(self):
        return self.subscription_code or f"Subscription #{self.pk}"


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
    # Postgres GENERATED column (check_out - check_in in minutes) — read-only, never write to it.
    duration_min = models.IntegerField(null=True, blank=True, editable=False)

    class Meta:
        managed = False
        db_table = 'attendance'
        unique_together = ('member', 'date')

    def __str__(self):
        return f"{self.member} - {self.date}"


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


class NotificationSetting(models.Model):
    """A notification preference toggle — maps to core_notificationsetting."""

    code = models.CharField(max_length=50, null=True, blank=True)
    name = models.CharField(max_length=150, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    enabled = models.BooleanField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'core_notificationsetting'
        ordering = ['id']

    def __str__(self):
        return self.name or self.code or f"Setting #{self.pk}"

    @classmethod
    def is_enabled(cls, code):
        try:
            return bool(cls.objects.get(code=code).enabled)
        except cls.DoesNotExist:
            return True


class NotificationRead(models.Model):
    """Tracks which notification keys have been marked as read."""

    nkey = models.CharField(max_length=120, unique=True)
    read_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'app_notificationread'
        ordering = ['-read_at']

    def __str__(self):
        return self.nkey


class Expense(models.Model):
    """A single facility expense — maps to the existing 'expenses' table."""

    CATEGORY_CHOICES = [
        ('rent', 'Rent'),
        ('equipment', 'Equipment'),
        ('salaries', 'Salaries'),
        ('utilities', 'Utilities'),
        ('maintenance', 'Maintenance'),
        ('operations', 'Operations'),
        ('other', 'Other'),
    ]

    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('card', 'Card'),
        ('bank_transfer', 'Bank Transfer'),
        ('online', 'Online'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('cleared', 'Cleared'),
    ]

    expense_code = models.CharField(max_length=50, null=True, blank=True)
    category = models.CharField(max_length=50, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=50, null=True, blank=True)
    expense_date = models.DateField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'expenses'
        ordering = ['-expense_date', '-id']

    def __str__(self):
        return self.expense_code or f"Expense #{self.pk}"


class Payment(models.Model):
    """A payment received — maps to the existing 'payments' table."""

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
    subscription_id = models.IntegerField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'payments'
        ordering = ['-paid_at', '-id']

    @property
    def member_name(self):
        if self.member:
            return self.member.full_name
        return "Walk-in / Guest"

    def refunded_amount(self):
        """Total of non-rejected refunds already issued against this payment."""
        total = self.refunds.exclude(status='rejected').aggregate(Sum('amount'))['amount__sum']
        return total or Decimal('0.00')

    def refundable_amount(self):
        return (self.total or Decimal('0.00')) - self.refunded_amount()

    def __str__(self):
        return self.payment_code or f"Payment #{self.pk}"


class Refund(models.Model):
    """A refund issued against a payment — maps to the existing 'refunds' table."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    refund_code = models.CharField(max_length=50, null=True, blank=True)
    payment = models.ForeignKey(
        Payment,
        on_delete=models.CASCADE,
        db_column='payment_id',
        related_name='refunds',
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        db_column='member_id',
        null=True,
        blank=True,
        related_name='refunds',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'refunds'
        ordering = ['-created_at', '-id']

    def save(self, *args, **kwargs):
        if self.created_at is None:
            from django.utils import timezone
            self.created_at = timezone.now()
        if self.member_id is None and self.payment_id and self.payment.member_id:
            self.member_id = self.payment.member_id
        super().save(*args, **kwargs)

    def __str__(self):
        return self.refund_code or f"Refund #{self.pk}"
