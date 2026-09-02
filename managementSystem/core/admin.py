from django.contrib import admin

from .models import BusinessInformation, FinancialSetting, NotificationSetting, PaymentMethod, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("full_name", "email", "username", "role", "status", "updated_at")
    search_fields = ("full_name", "email", "username")


@admin.register(BusinessInformation)
class BusinessInformationAdmin(admin.ModelAdmin):
    list_display = ("business_name", "email", "phone", "updated_at")


@admin.register(FinancialSetting)
class FinancialSettingAdmin(admin.ModelAdmin):
    list_display = ("currency", "tax_rate", "updated_at")


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "enabled", "updated_at")
    list_editable = ("enabled",)


@admin.register(NotificationSetting)
class NotificationSettingAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "enabled", "updated_at")
    list_editable = ("enabled",)
