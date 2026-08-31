from django.conf import settings
from django.db import models


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
        settings.AUTH_USER_MODEL,
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
