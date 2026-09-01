from django.db import models


class MembershipPlan(models.Model):
    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=150, unique=True, null=True, blank=True)
    duration_days = models.IntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_fixed = models.BooleanField(null=True, blank=True, default=True)
    is_active = models.BooleanField(null=True, blank=True, default=True)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'membership_plans'

    def __str__(self):
        return self.name


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
