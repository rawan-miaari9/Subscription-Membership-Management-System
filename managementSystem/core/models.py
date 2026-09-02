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
