from django.db import models


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
