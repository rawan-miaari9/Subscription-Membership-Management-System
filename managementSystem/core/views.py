import json
import re
from decimal import Decimal

from django.contrib.auth import logout as auth_logout
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .models import BusinessInformation, FinancialSetting, NotificationSetting, PaymentMethod, User

def login_view(request):
    return render(request, "auth/login.html")


@require_http_methods(["POST"])
def logout_view(request):
    """Log out and redirect the admin to the login page."""
    auth_logout(request)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "redirect": "/login/"})
    return redirect("login-page")

def dashboard_view(request):
    return render(request, "dashboard/index.html")

def members_view(request):
    return render(request, "members/list.html")

def plans_view(request):
    return render(request, "plans/list.html")

def subscriptions_view(request):
    return render(request, "subscriptions/list.html")

def pricing_view(request):
    return render(request, "pricing/list.html")

def payments_view(request):
    return render(request, "payments/list.html")

def invoices_view(request):
    return render(request, "invoices/detail.html")

def renewals_view(request):
    return render(request, "renewals/list.html")

def refunds_view(request):
    return render(request, "refunds/list.html")

def attendance_view(request):
    return render(request, "attendance/index.html")

def expenses_view(request):
    return render(request, "expenses/list.html")

def notifications_view(request):
    return render(request, "notifications/index.html")

def reports_view(request):
    return render(request, "reports/index.html")

def users_view(request):
    return render(request, "users/list.html")

def settings_view(request):
    business = BusinessInformation.get_singleton()
    response = render(request, "settings/index.html", {
        "business": business,
        "financial": FinancialSetting.get_singleton(),
        "payment_methods": PaymentMethod.get_all(),
        "notification_settings": NotificationSetting.get_all(),
        "admin_user": User.get_admin(),
    })
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@require_http_methods(["GET", "POST"])
def financial_settings_api(request):
    financial = FinancialSetting.get_singleton()

    if request.method == "GET":
        return JsonResponse({
            "currency": financial.currency,
            "tax_rate": str(financial.tax_rate),
        })

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    if payload.get("currency", "USD") != "USD":
        return JsonResponse({"ok": False, "error": "Only USD is supported."}, status=400)

    try:
        tax_float = float(payload.get("tax_rate", financial.tax_rate))
    except (TypeError, ValueError):
        return JsonResponse({
            "ok": False,
            "error": "Tax rate must be a number between 0 and 100.",
        }, status=400)
    if tax_float < 0 or tax_float > 100:
        return JsonResponse({
            "ok": False,
            "error": "Tax rate must be between 0 and 100.",
        }, status=400)

    financial.currency = "USD"
    financial.tax_rate = Decimal(str(tax_float))
    financial.save()
    return JsonResponse({
        "ok": True,
        "currency": financial.currency,
        "tax_rate": str(financial.tax_rate),
    })


@require_http_methods(["GET", "POST"])
def business_settings_api(request):
    business = BusinessInformation.get_singleton()

    if request.method == "GET":
        return JsonResponse({
            "business_name": business.business_name,
            "phone": business.phone,
            "email": business.email,
            "address": business.address,
            "logo": business.logo.url if business.logo else "",
        })

    business_name = request.POST.get("business_name", "").strip()
    phone = request.POST.get("phone", "").strip()
    email = request.POST.get("email", "").strip()
    address = request.POST.get("address", "").strip()

    errors = []
    if not business_name:
        errors.append("Business name is required.")
    if not phone:
        errors.append("Phone is required.")
    elif not re.match(r"^[+\d][\d\s().-]*$", phone):
        errors.append("Valid phone is required.")
    if not email:
        errors.append("Email is required.")
    elif not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        errors.append("Valid email is required.")
    if not address:
        errors.append("Address is required.")
    if "logo" in request.FILES:
        logo = request.FILES["logo"]
        if logo.content_type not in ("image/png", "image/jpeg"):
            errors.append("Logo must be a PNG or JPG image.")
        elif logo.size > 2 * 1024 * 1024:
            errors.append("Logo must be under 2MB.")
    if errors:
        return JsonResponse({"ok": False, "error": " ".join(errors)}, status=400)

    business.business_name = business_name
    business.phone = phone
    business.email = email
    business.address = address
    if "logo" in request.FILES:
        business.logo = request.FILES["logo"]
    business.save()
    return JsonResponse({
        "ok": True,
        "business_name": business.business_name,
        "phone": business.phone,
        "email": business.email,
        "address": business.address,
        "logo": business.logo.url if business.logo else "",
        "updated_at": business.updated_at.isoformat(),
    })


@require_http_methods(["GET", "POST"])
def payment_settings_api(request):
    if request.method == "GET":
        return JsonResponse({
            "methods": [
                {"code": m.code, "name": m.name, "enabled": m.enabled}
                for m in PaymentMethod.get_all()
            ],
        })

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    methods = PaymentMethod.get_all()
    next_enabled = {m.code: bool(payload.get(m.code, False)) for m in methods}
    if not any(next_enabled.values()):
        return JsonResponse({
            "ok": False,
            "error": "At least one payment method must be enabled.",
        }, status=400)

    for m in methods:
        if m.enabled != next_enabled[m.code]:
            m.enabled = next_enabled[m.code]
            m.save()

    return JsonResponse({
        "ok": True,
        "methods": [
            {"code": m.code, "name": m.name, "enabled": m.enabled}
            for m in methods
        ],
    })


@require_http_methods(["GET", "POST"])
def notification_settings_api(request):
    if request.method == "GET":
        return JsonResponse({
            "settings": [
                {"code": n.code, "name": n.name, "enabled": n.enabled}
                for n in NotificationSetting.get_all()
            ],
        })

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    for n in NotificationSetting.get_all():
        if n.code in payload:
            n.enabled = bool(payload[n.code])
            n.save()

    return JsonResponse({
        "ok": True,
        "settings": [
            {"code": n.code, "name": n.name, "enabled": n.enabled}
            for n in NotificationSetting.get_all()
        ],
    })


@require_http_methods(["GET", "POST"])
def admin_profile_settings_api(request):
    admin = User.get_admin()

    if request.method == "GET":
        return JsonResponse({
            "full_name": admin.full_name,
            "email": admin.email,
            "username": admin.username,
            "role": admin.role,
            "status": admin.status,
            "avatar": admin.avatar.url if admin.avatar else "",
        })

    full_name = (request.POST.get("full_name") or "").strip()
    email = (request.POST.get("email") or "").strip()
    username = (request.POST.get("username") or "").strip()
    role = (request.POST.get("role") or "").strip()
    status = (request.POST.get("status") or "").strip()

    errors = []
    if not full_name:
        errors.append("Full name is required.")
    elif not re.match(r"^[A-Za-z ]+$", full_name):
        errors.append("Full name may contain letters and spaces only.")
    if not email:
        errors.append("Email is required.")
    elif not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        errors.append("Valid email is required.")
    if not username:
        errors.append("Username is required.")
    elif not re.match(r"^[A-Za-z0-9_]+$", username):
        errors.append("Username may contain letters, numbers, and underscores only.")
    if role not in ("Admin", "Accountant"):
        errors.append("Role must be Admin or Accountant.")
    if status not in ("Active", "Inactive"):
        errors.append("Status must be Active or Inactive.")
    if not errors:
        if User.objects.filter(email=email).exclude(pk=admin.pk).exists():
            errors.append("Email is already in use.")
        if User.objects.filter(username=username).exclude(pk=admin.pk).exists():
            errors.append("Username is already in use.")
    if errors:
        return JsonResponse({"ok": False, "error": " ".join(errors)}, status=400)

    admin.full_name = full_name
    admin.email = email
    admin.username = username
    admin.role = role
    admin.status = status
    if "avatar" in request.FILES:
        if admin.avatar:
            admin.avatar.delete(save=False)
        admin.avatar = request.FILES["avatar"]
    admin.save()

    return JsonResponse({
        "ok": True,
        "full_name": admin.full_name,
        "email": admin.email,
        "username": admin.username,
        "role": admin.role,
        "status": admin.status,
        "avatar": admin.avatar.url if admin.avatar else "",
    })


def member_detail_view(request):
    return render(request, "members/detail.html")

def member_add_view(request):
    return render(request, "members/add.html")

def services_view(request):
    return render(request, "services/list.html")

def plan_create_view(request):
    return render(request, "plans/create.html")

def plan_custom_view(request):
    return render(request, "plans/custom.html")

def subscription_detail_view(request):
    return render(request, "subscriptions/detail.html")

def subscription_create_view(request):
    return render(request, "subscriptions/create.html")

def payment_detail_view(request):
    return render(request, "payments/detail.html")

def refund_detail_view(request):
    return render(request, "refunds/detail.html")

def refund_history_view(request):
    return render(request, "refunds/history.html")

def statement_view(request):
    return render(request, "statement/list.html")

def attendance_checkin_view(request):
    return render(request, "attendance/checkin.html")

def expense_add_view(request):
    return render(request, "expenses/add.html")

def user_add_view(request):
    return render(request, "users/add.html")

def reports_generate_view(request):
    return render(request, "reports/generate.html")

def receipt_custom_view(request):
    return render(request, "receipts/custom.html")
