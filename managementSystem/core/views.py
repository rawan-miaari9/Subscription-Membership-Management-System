import json
import re
from calendar import month_abbr
from decimal import Decimal

from django.contrib.auth import logout as auth_logout
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.timezone import localdate
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

def dashboard_data():
    """Collect all dashboard data from existing tables and return it as a dict."""

    def fetch(sql, params=None):
        with connection.cursor() as cur:
            cur.execute(sql, params or [])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    with connection.cursor() as cur:
        # 1. Active members
        cur.execute("SELECT COUNT(*) AS n FROM members WHERE status = 'active'")
        active_members = cur.fetchone()[0]

        # 2. Monthly revenue (current month)
        cur.execute(
            "SELECT COALESCE(SUM(total), 0) AS total FROM payments "
            "WHERE status = 'success' "
            "AND date_trunc('month', paid_at) = date_trunc('month', CURRENT_DATE)"
        )
        monthly_revenue = cur.fetchone()[0] or 0

        # 3. Revenue overview (last 6 months)
        cur.execute(
            "SELECT to_char(date_trunc('month', paid_at), 'YYYY-MM') AS ym, "
            "       COALESCE(SUM(total), 0) AS total "
            "FROM payments WHERE status = 'success' "
            "AND paid_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '5 months' "
            "GROUP BY date_trunc('month', paid_at) "
            "ORDER BY date_trunc('month', paid_at)"
        )
        revenue_rows = cur.fetchall()
        revenue_by_month = {r[0]: (r[1] or 0) for r in revenue_rows}

        # 4. Expiring soon (active subscriptions expiring within 30 days)
        cur.execute(
            "SELECT COUNT(*) AS n FROM subscriptions "
            "WHERE status = 'active' AND end_date BETWEEN CURRENT_DATE "
            "AND CURRENT_DATE + INTERVAL '30 days'"
        )
        expiring_soon = cur.fetchone()[0]

        # 5. Today's attendance
        cur.execute(
            "SELECT COUNT(*) AS n FROM attendance WHERE date = CURRENT_DATE"
        )
        todays_attendance = cur.fetchone()[0]

    # Expiring subscriptions: the 4 subscriptions closest to expiring
    today = localdate()
    expiring_subs = fetch(
        "SELECT s.id, m.member_code, m.full_name, "
        "       COALESCE(p.name, 'N/A') AS plan_name, s.end_date "
        "FROM subscriptions s "
        "LEFT JOIN members m ON m.id = s.member_id "
        "LEFT JOIN membership_plans p ON p.id = s.plan_id "
        "WHERE s.status = 'active' AND s.end_date >= CURRENT_DATE "
        "ORDER BY s.end_date ASC LIMIT 4"
    )
    expiring_subscriptions = []
    for s in expiring_subs:
        days = (s["end_date"] - today).days
        if days < 1:
            days = 1
        expiring_subscriptions.append({
            "member_id": s["member_code"] or ("#" + str(s["id"])),
            "name": s["full_name"] or "Unknown",
            "plan": s["plan_name"],
            "end_date": s["end_date"].isoformat(),
            "days": days,
        })

    # Recent payments (latest 4)
    recent_payments_rows = fetch(
        "SELECT p.payment_code, p.receipt_no, p.total, p.status, p.method, "
        "       p.paid_at, m.full_name "
        "FROM payments p LEFT JOIN members m ON m.id = p.member_id "
        "ORDER BY p.paid_at DESC LIMIT 4"
    )
    recent_payments = []
    for p in recent_payments_rows:
        recent_payments.append({
            "member": p["full_name"] or "Unknown",
            "receipt_no": p["receipt_no"] or p["payment_code"] or "-",
            "amount": str(p["total"]),
            "status": (p["status"] or "success").upper(),
            "method": p["method"] or "",
            "paid_at": p["paid_at"].isoformat() if p["paid_at"] else "",
        })

    # Revenue overview: last 6 calendar months
    revenue_overview = []
    year, month = today.year, today.month
    for i in range(5, -1, -1):
        y, m = year, month - i
        while m <= 0:
            m += 12
            y -= 1
        ym = "%04d-%02d" % (y, m)
        revenue_overview.append({
            "month": ym,
            "label": month_abbr[m].upper(),
            "total": float(revenue_by_month.get(ym, 0)),
        })

    # Subscription mix: active subscriptions grouped by plan
    mix_rows = fetch(
        "SELECT COALESCE(p.name, 'Other') AS name, COUNT(*) AS n "
        "FROM subscriptions s "
        "LEFT JOIN membership_plans p ON p.id = s.plan_id "
        "WHERE s.status = 'active' "
        "GROUP BY COALESCE(p.name, 'Other') "
        "ORDER BY n DESC"
    )
    total_subs = sum(r["n"] for r in mix_rows) or 0
    subscription_mix = []
    for r in mix_rows:
        pct = round((r["n"] / total_subs * 100) if total_subs else 0)
        subscription_mix.append({
            "plan": r["name"],
            "count": r["n"],
            "percent": pct,
        })

    return {
        "ok": True,
        "active_members": active_members,
        "monthly_revenue": monthly_revenue,
        "revenue_overview": revenue_overview,
        "recent_payments": recent_payments,
        "expiring_soon": expiring_soon,
        "expiring_subscriptions": expiring_subscriptions,
        "todays_attendance": todays_attendance,
        "subscription_mix": subscription_mix,
        "subscription_mix_total": total_subs,
    }


def dashboard_context():
    """Return a template-friendly version of the dashboard data."""
    data = dashboard_data()
    ctx = dict(data)

    ctx["active_members_display"] = f"{data['active_members']:,}"
    ctx["expiring_soon_display"] = f"{data['expiring_soon']:,}"
    ctx["todays_attendance_display"] = f"{data['todays_attendance']:,}"
    ctx["monthly_revenue_display"] = "$" + f"{float(data['monthly_revenue']):,.2f}"

    if data["subscription_mix_total"] >= 1000:
        amt = data["subscription_mix_total"] / 1000
        ctx["subscription_mix_compact"] = (f"{amt:.1f}k" if amt % 1 else f"{amt:.0f}k")
    else:
        ctx["subscription_mix_compact"] = str(data["subscription_mix_total"])

    overview = data["revenue_overview"]
    max_total = max((d["total"] for d in overview), default=0)
    if max_total <= 0:
        max_total = 1
    for d in overview:
        height = round((d["total"] / max_total) * 85)
        if height < 3:
            height = 3
        d["height"] = height
        d["total_display"] = "$" + f"{d['total']:,.0f}"
        d["is_last"] = (d["month"] == overview[-1]["month"])
    ctx["revenue_overview"] = overview

    mix = data["subscription_mix"]
    mix_total = data["subscription_mix_total"]
    ctx["mix_gradient"] = mix_gradient(mix, mix_total)

    mix_swatches = ["bg-primary", "bg-outline", "bg-surface-container-highest", "bg-tertiary", "bg-primary-container", "bg-outline-variant"]
    for idx, item in enumerate(mix):
        item["swatch"] = mix_swatches[idx % len(mix_swatches)]
    ctx["subscription_mix"] = mix

    method_icons = {"cash": "payments", "card": "credit_card", "transfer": "account_balance", "online": "language"}
    for p in ctx["recent_payments"]:
        p["success"] = (p["status"] == "SUCCESS")
        p["method_icon"] = method_icons.get(p["method"], "credit_card")
        p["amount_display"] = "$" + f"{float(p['amount']):,.2f}"

    return ctx


MIX_COLORS = [
    "var(--primary)",
    "var(--outline)",
    "var(--surface-container-highest)",
    "var(--tertiary)",
    "var(--primary-container)",
    "var(--outline-variant)",
]


def mix_gradient(mix, total):
    """Build a conic-gradient string representing the subscription mix."""
    if total <= 0 or not mix:
        return "var(--surface-container-highest)"
    stops = []
    acc = 0
    for idx, item in enumerate(mix):
        color = MIX_COLORS[idx % len(MIX_COLORS)]
        pct = (item["count"] / total) * 100
        stops.append(f"{color} {acc}% {acc + pct}%")
        acc += pct
    return "conic-gradient(" + ", ".join(stops) + ")"


def dashboard_view(request):
    return render(request, "dashboard/index.html", dashboard_context())


@require_http_methods(["GET"])
def dashboard_api(request):
    """Return all data needed by the dashboard, sourced from existing tables."""
    data = dashboard_data()
    data["monthly_revenue"] = str(data["monthly_revenue"])
    return JsonResponse(data)

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
