from django.db import models
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
