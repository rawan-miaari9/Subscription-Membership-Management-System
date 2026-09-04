from calendar import month_abbr
import datetime
import json
import re
from functools import wraps
from decimal import Decimal, InvalidOperation
from datetime import date, timedelta
from django.core.cache import cache
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, connection, transaction
from django.db.models import Case, DecimalField, F, Q, Sum, When
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.timezone import localdate
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.contrib.auth.hashers import make_password

from .forms import MemberForm, PaymentForm, PlanForm, ServiceForm, UserAddForm
from .permissions import role_required

from .models import User, Member, MembershipPlan, Subscription, BusinessInformation, FinancialSetting, PaymentMethod, NotificationSetting, Payment, Attendance, Service, PlanService, UserProfile, Invoice, Receipt, Financial, Promotion

def _decimal(value, default=Decimal('0.00')):
    try:
        return Decimal(str(value).strip())
    except (TypeError, ValueError, InvalidOperation):
        return default


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None

def _checkin_block_reason(member, today):
    """Return (reason, subscription_status). reason is None when check-in is allowed.

    "Current" subscription = the one with the latest end_date for this member —
    picking by furthest-out end_date (rather than requiring start_date <= today
    <= end_date) means a member whose latest subscription has already lapsed still
    gets a specific "expired on <date>" reason instead of a generic "not found",
    and it tolerates early renewals where the newest row hasn't started yet.
    """
    subscription = Subscription.objects.filter(member=member).order_by('-end_date').first()

    if subscription is None:
        return "No active subscription found.", None

    status = subscription.status
    end_date_display = subscription.end_date.strftime('%b %d, %Y') if subscription.end_date else 'an unknown date'

    if status == 'suspended':
        return "Subscription is suspended, contact admin.", status

    if status == 'cancelled':
        return "Subscription was cancelled.", status

    if status == 'expired' or (subscription.end_date and subscription.end_date < today):
        # Covers an explicit 'expired' status AND stale data where status still
        # says active/expiring but end_date has already passed — don't trust
        # the status field blindly.
        return f"Subscription expired on {end_date_display}.", status

    if status in ('active', 'expiring'):
        return None, status

    return "Subscription status could not be verified.", status



def per_user_page_cache(timeout=None):
    """Cache full page per user for 5 minutes - second visit 0.01s without DB"""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.method != "GET":
                return view_func(request, *args, **kwargs)
            # don't cache if messages exist (after create/renew)
            from django.contrib.messages import get_messages
            if list(get_messages(request)):
                # need to re-store messages for next view
                return view_func(request, *args, **kwargs)
            timeout_val = timeout or getattr(settings, "PAGE_CACHE_TIMEOUT", 300)
            user_id = request.session.get("user_id", "anon")
            cache_key = f"page:{view_func.__name__}:{user_id}:{request.get_full_path()}"
            cached = cache.get(cache_key)
            if cached:
                return cached
            response = view_func(request, *args, **kwargs)
            if response.status_code == 200:
                # store a copy - need to make it cacheable
                response._is_cached = True
                cache.set(cache_key, response, timeout_val)
            return response
        return wrapper
    return decorator


def get_current_user(request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    # cache user for 10 minutes to avoid DB hit on every navigation
    cache_key = f"user_{user_id}"
    user = cache.get(cache_key)
    if user is not None:
        return user
    try:
        user = User.objects.get(id=user_id)
        cache.set(cache_key, user, 60 * 10)
        return user
    except User.DoesNotExist:
        return None


def login_required_custom(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = get_current_user(request)
        if not user:
            next_url = request.get_full_path()
            if request.path != "/login/" and request.path != "/":
                return redirect(f"/login/?next={next_url}")
            return redirect("/login/")
        request.current_user = user
        return view_func(request, *args, **kwargs)
    return wrapper


def login_view(request):
    if get_current_user(request):
        return redirect("dashboard")

    error = None
    email_prefill = ""

    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        role = request.POST.get("role", "").strip()
        remember = request.POST.get("remember_me")
        next_url = request.POST.get("next") or request.GET.get("next")
        email_prefill = email

        if not email or not password:
            error = "Please fill email and password."
        elif "@" not in email:
            error = "Invalid email format."
        else:
            try:
                user = User.objects.get(email__iexact=email)
            except User.DoesNotExist:
                error = "Invalid email or password."
            else:
                if user.status != "Active":
                    error = "Account is not active. Contact admin."
                elif role and user.role.lower() != role.lower():
                    error = f"Role mismatch. Your account is {user.role}."
                elif not user.check_password(password):
                    error = "Invalid email or password."
                else:
                    request.session["user_id"] = user.id
                    request.session["user_email"] = user.email
                    request.session["user_role"] = user.role
                    request.session["user_name"] = user.full_name
                    request.session["username"] = user.username
                    request.session["avatar"] = user.avatar or ""
                    if remember:
                        request.session.set_expiry(60 * 60 * 24 * 30)
                    else:
                        request.session.set_expiry(0)
                    User.objects.filter(id=user.id).update(updated_at=timezone.now())
                    if next_url and next_url.startswith("/"):
                        return redirect(next_url)
                    return redirect("dashboard")

    context = {
        "error": error,
        "email": email_prefill,
        "next": request.GET.get("next", ""),
    }
    return render(request, "auth/login.html", context)


def logout_view(request):
    request.session.flush()
    messages.success(request, "You have been logged out.")
    return redirect("login")


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

@login_required_custom
@per_user_page_cache(300)
def dashboard_view(request):
    return render(request, "dashboard/index.html", dashboard_context())


@require_http_methods(["GET"])

@require_http_methods(["GET"])
def dashboard_api(request):
    """Return all data needed by the dashboard, sourced from existing tables."""
    data = dashboard_data()
    data["monthly_revenue"] = str(data["monthly_revenue"])
    return JsonResponse(data)

@login_required_custom

@login_required_custom
def members_view(request):
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()

    # Newest members first — the most useful default for an admin glancing at
    # who just joined; '-id' breaks ties for same-day joins deterministically.
    members_qs = Member.objects.all().order_by('-join_date', '-id')
    if search_query:
        members_qs = members_qs.filter(
            Q(full_name__icontains=search_query)
            | Q(member_code__icontains=search_query)
            | Q(phone__icontains=search_query)
            | Q(email__icontains=search_query)
        )
    if status_filter in dict(Member.STATUS_CHOICES):
        members_qs = members_qs.filter(status=status_filter)

    paginator = Paginator(members_qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Preserve active filters across Previous/Next links (drop 'page' — the link supplies its own).
    filters_querydict = request.GET.copy()
    filters_querydict.pop('page', None)
    filters_querystring = filters_querydict.urlencode()

    context = {
        'members': page_obj.object_list,
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'filters_querystring': filters_querystring,
        'filters_active': bool(search_query or status_filter),
    }
    return render(request, "members/list.html", context)

@login_required_custom
def plans_view(request):
    plans_qs = MembershipPlan.objects.all().order_by('name')
    paginator = Paginator(plans_qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    context = {
        'plans': page_obj.object_list,
        'page_obj': page_obj,
    }
    return render(request, "plans/list.html", context)

def plan_activate_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    plan = get_object_or_404(MembershipPlan, pk=pk)

    if plan.is_active:
        messages.info(request, f'"{plan.name}" is already active.')
    else:
        plan.is_active = True
        plan.save(update_fields=['is_active'])
        messages.success(request, f'"{plan.name}" was activated.')

    return redirect('plans')

def plan_deactivate_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    plan = get_object_or_404(MembershipPlan, pk=pk)

    if not plan.is_active:
        messages.info(request, f'"{plan.name}" is already inactive.')
    else:
        plan.is_active = False
        plan.save(update_fields=['is_active'])
        messages.success(request, f'"{plan.name}" was deactivated.')

    return redirect('plans')

@login_required_custom
@per_user_page_cache(300)
def subscriptions_view(request):
    today = date.today()
    # Auto-suspend only once per hour (cached) to avoid UPDATE on every reload
    cache_key_suspend = f"auto_suspend_{today.isoformat()}"
    if not cache.get(cache_key_suspend):
        Subscription.objects.filter(status__in=["active", "expiring"], end_date__lt=today).update(status="suspended")
        cache.set(cache_key_suspend, True, 60 * 60)  # 1 hour

    subs = Subscription.objects.select_related("member", "plan").order_by("-created_at")

    # Cache metrics for 5 minutes to reduce 3 COUNT/SUM queries
    cache_key_metrics = "sub_metrics"
    metrics = cache.get(cache_key_metrics)
    if metrics is None:
        active_count = Subscription.objects.filter(status="active").count()
        expiring_count = Subscription.objects.filter(
            status="active",
            end_date__gte=today,
            end_date__lte=today + timedelta(days=7)
        ).count()
        from django.db.models import Sum
        mrr = Subscription.objects.filter(status="active").aggregate(total=Sum("plan__price"))["total"] or 0
        metrics = {"active_count": active_count, "expiring_count": expiring_count, "mrr": mrr}
        cache.set(cache_key_metrics, metrics, 60 * 5)  # 5 minutes
    else:
        active_count = metrics["active_count"]
        expiring_count = metrics["expiring_count"]
        mrr = metrics["mrr"]
    return render(request, "subscriptions/list.html", {
        "current_user": get_current_user(request),
        "subscriptions": subs,
        "active_count": active_count,
        "expiring_count": expiring_count,
        "mrr": mrr,
    })

def pricing_view(request):
    user = get_current_user(request)
    if not user:
        return redirect("/login/?next=/pricing/")
    return render(request, "pricing/list.html", {"current_user": user})

@login_required_custom
@login_required_custom
def payments_view(request):
    # Handle POST: process new payment via PaymentForm
    if request.method == "POST":
        form = PaymentForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            member = data.get('member_obj')
            subscription = data.get('subscription_obj')
            amount = data['amount']
            method = data['method']
            status = data['status']
            payment_model = data.get('payment_model', 'full')
            # Enforce: Full always Paid (success), Partial can be Pending
            if payment_model == 'full':
                status = 'success'
            # Generate codes with retry for sequence issues
            try:
                from django.db import connection as conn
                with conn.cursor() as cur:
                    cur.execute("SELECT setval(pg_get_serial_sequence('payments','id'), COALESCE((SELECT MAX(id) FROM payments),0)+1, false)")
            except Exception:
                pass
            payment_code = PaymentForm.generate_payment_code()
            receipt_no = PaymentForm.generate_receipt_no()
            # Ensure uniqueness retry
            for attempt in range(3):
                try:
                    payment = Payment.objects.create(
                        payment_code=payment_code,
                        receipt_no=receipt_no,
                        member=member,
                        subscription=subscription,
                        amount=amount,
                        discount=Decimal('0.00'),
                        total=amount,
                        method=method,
                        status=status,
                        paid_at=timezone.now() if status == 'success' else None,
                    )
                    break
                except IntegrityError as e:
                    if 'duplicate key' in str(e).lower() and attempt < 2:
                        # Regenerate codes
                        payment_code = PaymentForm.generate_payment_code()
                        receipt_no = PaymentForm.generate_receipt_no()
                        continue
                    raise
            # Update Member.balance for Partial/Full
            try:
                if subscription and subscription.plan and subscription.plan.price is not None:
                    plan_price = subscription.plan.price
                    if payment_model == 'partial':
                        remaining = max(Decimal('0.00'), plan_price - amount)
                        Member.objects.filter(id=member.id).update(balance=remaining)
                    else:  # full
                        Member.objects.filter(id=member.id).update(balance=Decimal('0.00'))
                elif payment_model == 'partial':
                    # Ad-hoc partial without plan: keep amount as pending balance
                    # Use existing balance + remaining? For now set to amount pending
                    pass
                else:
                    Member.objects.filter(id=member.id).update(balance=Decimal('0.00'))
            except Exception:
                pass
            if payment_model == 'partial' and subscription and subscription.plan:
                try:
                    remaining = max(Decimal('0.00'), subscription.plan.price - amount)
                    messages.success(request, f"Payment {payment_code} for {member.full_name} (${amount:.2f}) recorded. Remaining balance: ${remaining:.2f} (Plan ${subscription.plan.price:.2f})")
                except Exception:
                    messages.success(request, f"Payment {payment_code} for {member.full_name} (${amount:.2f}) recorded.")
            else:
                messages.success(request, f"Payment {payment_code} for {member.full_name} (${amount:.2f}) recorded.")
            return redirect('payments')
        else:
            # Form errors - will be displayed in template
            pass
    else:
        form = PaymentForm()

    # GET: list with filters
    payments_qs = Payment.objects.select_related('member', 'subscription').order_by('-paid_at', '-id')
    search = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    method_filter = request.GET.get('method', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    if search:
        payments_qs = payments_qs.filter(
            Q(payment_code__icontains=search) |
            Q(receipt_no__icontains=search) |
            Q(member__full_name__icontains=search) |
            Q(member__member_code__icontains=search)
        )
    if status_filter in dict(Payment.STATUS_CHOICES):
        payments_qs = payments_qs.filter(status=status_filter)
    if method_filter in dict(Payment.METHOD_CHOICES):
        payments_qs = payments_qs.filter(method=method_filter)
    if date_from:
        try:
            d = datetime.date.fromisoformat(date_from)
            payments_qs = payments_qs.filter(paid_at__date__gte=d)
        except ValueError:
            pass
    if date_to:
        try:
            d = datetime.date.fromisoformat(date_to)
            payments_qs = payments_qs.filter(paid_at__date__lte=d)
        except ValueError:
            pass

    paginator = Paginator(payments_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))
    filters_qd = request.GET.copy()
    filters_qd.pop('page', None)
    filters_qs = filters_qd.urlencode()

    # Stats for bento cards
    try:
        total_paid = Payment.objects.filter(status='success').aggregate(s=Sum('total'))['s'] or 0
        pending_count = Payment.objects.filter(status='pending').count()
    except Exception:
        total_paid = 0
        pending_count = 0

    return render(request, "payments/list.html", {
        "current_user": get_current_user(request),
        "form": form,
        "payments": page_obj.object_list,
        "page_obj": page_obj,
        "search_query": search,
        "status_filter": status_filter,
        "method_filter": method_filter,
        "date_from": date_from,
        "date_to": date_to,
        "filters_querystring": filters_qs,
        "filters_active": bool(search or status_filter or method_filter or date_from or date_to),
        "total_paid": total_paid,
        "pending_count": pending_count,
    })

@login_required_custom
def invoices_view(request):
    invoices_qs = Invoice.objects.select_related('member').order_by('-issued_date', '-id')

    status = request.GET.get('status', '').strip()
    search = request.GET.get('q', '').strip()
    today = timezone.localdate()

    if search:
        invoices_qs = invoices_qs.filter(
            Q(invoice_no__icontains=search) | Q(member__full_name__icontains=search)
        )
    if status == 'overdue':
        invoices_qs = invoices_qs.exclude(status__in=['paid', 'void']).filter(
            due_date__lt=today, amount_paid__lt=F('total')
        )
    elif status == 'partial':
        invoices_qs = invoices_qs.filter(amount_paid__gt=0, amount_paid__lt=F('total'))
    elif status == 'paid':
        invoices_qs = invoices_qs.filter(status='paid')
    elif status in ('draft', 'sent', 'void'):
        invoices_qs = invoices_qs.filter(status=status)

    paginator = Paginator(invoices_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Preserve active filters across Previous/Next links.
    filters_querydict = request.GET.copy()
    filters_querydict.pop('page', None)
    filters_querystring = filters_querydict.urlencode()

    # Summary stats (only for the current filter set).
    stats_qs = invoices_qs
    totals = stats_qs.aggregate(
        total_billed=Sum('total'),
        total_paid=Sum('amount_paid'),
    )
    outstanding = (totals['total_billed'] or 0) - (totals['total_paid'] or 0)
    overdue_count = stats_qs.exclude(status__in=['paid', 'void']).filter(
        due_date__lt=today, amount_paid__lt=F('total')
    ).count()

    context = {
        'invoices': page_obj.object_list,
        'page_obj': page_obj,
        'filters_querystring': filters_querystring,
        'filter_status': status,
        'filter_search': search,
        'filters_active': bool(status or search),
        'stat_total': stats_qs.count(),
        'stat_billed': totals['total_billed'] or 0,
        'stat_outstanding': outstanding,
        'stat_overdue': overdue_count,
        'today': today,
    }
    return render(request, "invoices/list.html", context)

def _next_invoice_no():
    year = timezone.now().year
    prefix = f"INV-{year}-"
    count = Invoice.objects.filter(invoice_no__startswith=prefix).count()
    number = count + 1
    while True:
        candidate = f"{prefix}{number:04d}"
        if not Invoice.objects.filter(invoice_no=candidate).exists():
            return candidate
        number += 1

def invoice_create_view(request):
    if request.method == "POST":
        member_id = request.POST.get('member_id') or None
        bill_to = request.POST.get('bill_to', '').strip()
        description = request.POST.get('description', '').strip()
        amount = _decimal(request.POST.get('amount'))
        discount_type = request.POST.get('discount_type', 'flat').strip()
        discount_value = _decimal(request.POST.get('discount_value'))
        tax_rate = Financial.get_tax_rate()
        payment_terms = request.POST.get('payment_terms', 'due_on_receipt').strip()
        issued_date = _parse_date(request.POST.get('issued_date')) or timezone.localdate()
        due_date = _parse_date(request.POST.get('due_date'))
        status = request.POST.get('status', 'draft').strip()
        amount_paid = _decimal(request.POST.get('amount_paid'))
        invoice_no = request.POST.get('invoice_no', '').strip()
        notes = request.POST.get('notes', '').strip()

        member = None
        if member_id:
            try:
                member = Member.objects.get(pk=member_id)
            except (Member.DoesNotExist, ValueError):
                return render(request, "invoices/create.html", {
                    'error': "Selected member no longer exists.",
                })

        if invoice_no and Invoice.objects.filter(invoice_no=invoice_no).exclude(pk=None).exists():
            invoice_no = _next_invoice_no()
        if not invoice_no:
            invoice_no = _next_invoice_no()

        invoice = Invoice(
            invoice_no=invoice_no,
            bill_to=bill_to or None,
            member=member,
            description=description or None,
            subtotal=amount,
            discount_type=discount_type if discount_type in ('flat', 'percent') else 'flat',
            discount=discount_value,
            tax_rate=tax_rate,
            payment_terms=payment_terms,
            issued_date=issued_date,
            due_date=due_date,
            status=status if status in dict(Invoice.STATUS_CHOICES) else 'draft',
            amount_paid=amount_paid,
            notes=notes or None,
        )
        invoice.recalculate()
        if invoice.status == 'paid':
            invoice.amount_paid = invoice.total

        try:
            invoice.save()
        except IntegrityError:
            invoice.invoice_no = _next_invoice_no()
            invoice.save()

        return redirect('invoice-detail', pk=invoice.pk)

    context = {
        'terms': Invoice.TERM_CHOICES,
        'status_choices': Invoice.STATUS_CHOICES,
        'next_invoice_no': _next_invoice_no(),
        'today': timezone.localdate(),
        'settings_tax_rate': Financial.get_tax_rate(),
    }
    return render(request, "invoices/create.html", context)

def invoice_edit_view(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)

    if request.method == "POST":
        member_id = request.POST.get('member_id') or None
        bill_to = request.POST.get('bill_to', '').strip()
        description = request.POST.get('description', '').strip()
        amount = _decimal(request.POST.get('amount'))
        discount_type = request.POST.get('discount_type', 'flat').strip()
        discount_value = _decimal(request.POST.get('discount_value'))
        tax_rate = Financial.get_tax_rate()
        payment_terms = request.POST.get('payment_terms', 'due_on_receipt').strip()
        issued_date = _parse_date(request.POST.get('issued_date')) or timezone.localdate()
        due_date = _parse_date(request.POST.get('due_date'))
        status = request.POST.get('status', 'draft').strip()
        amount_paid = _decimal(request.POST.get('amount_paid'))
        invoice_no = request.POST.get('invoice_no', '').strip() or invoice.invoice_no
        notes = request.POST.get('notes', '').strip()

        member = None
        if member_id:
            try:
                member = Member.objects.get(pk=member_id)
            except (Member.DoesNotExist, ValueError):
                return render(request, "invoices/create.html", {
                    'invoice': invoice,
                    'error': "Selected member no longer exists.",
                })

        if invoice_no and Invoice.objects.filter(invoice_no=invoice_no).exclude(pk=invoice.pk).exists():
            invoice_no = _next_invoice_no()

        invoice.invoice_no = invoice_no
        invoice.bill_to = bill_to or None
        invoice.member = member
        invoice.description = description or None
        invoice.subtotal = amount
        invoice.discount_type = discount_type if discount_type in ('flat', 'percent') else 'flat'
        invoice.discount = discount_value
        invoice.tax_rate = tax_rate
        invoice.payment_terms = payment_terms
        invoice.issued_date = issued_date
        invoice.due_date = due_date
        invoice.status = status if status in dict(Invoice.STATUS_CHOICES) else 'draft'
        invoice.amount_paid = amount_paid
        invoice.notes = notes or None
        invoice.recalculate()
        if invoice.status == 'paid':
            invoice.amount_paid = invoice.total

        try:
            invoice.save()
        except IntegrityError:
            invoice.invoice_no = _next_invoice_no()
            invoice.save()

        return redirect('invoice-detail', pk=invoice.pk)

    context = {
        'invoice': invoice,
        'terms': Invoice.TERM_CHOICES,
        'status_choices': Invoice.STATUS_CHOICES,
        'next_invoice_no': _next_invoice_no(),
        'today': timezone.localdate(),
        'settings_tax_rate': Financial.get_tax_rate(),
    }
    return render(request, "invoices/create.html", context)

def invoice_detail_view(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('member'), pk=pk)
    return render(request, "invoices/detail.html", {'invoice': invoice, 'today': timezone.localdate()})

def invoice_pdf_view(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('member'), pk=pk)
    try:
        from weasyprint import HTML
        html = render_to_string("invoices/pdf.html", {'invoice': invoice})
        pdf_bytes = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        if request.GET.get('debug'):
            return HttpResponse(
                f"PDF error: {type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
                .replace('\n', '<br>'),
                status=500,
            )
        return HttpResponse(
            "PDF rendering failed. The invoice cannot be exported right now; "
            "try View in browser or contact support.",
            status=503,
        )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    disposition = "attachment" if request.GET.get('download') == '1' else "inline"
    response['Content-Disposition'] = f'{disposition}; filename="{invoice.invoice_no}.pdf"'
    return response

def invoice_status_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    invoice = get_object_or_404(Invoice, pk=pk)
    action = request.POST.get('action')
    if action == 'mark_paid':
        invoice.amount_paid = invoice.total
        invoice.status = 'paid'
    elif action == 'mark_sent':
        invoice.status = 'sent'
    elif action == 'void':
        invoice.status = 'void'
    else:
        return JsonResponse({'error': 'Unknown action.'}, status=400)
    invoice.save()
    return redirect('invoice-detail', pk=invoice.pk)

def receipts_view(request):
    receipts_qs = Receipt.objects.select_related('member').order_by('-paid_date', '-id')

    search = request.GET.get('q', '').strip()
    method = request.GET.get('method', '').strip()

    if search:
        receipts_qs = receipts_qs.filter(
            Q(receipt_no__icontains=search) | Q(member__full_name__icontains=search) | Q(bill_to__icontains=search)
        )
    if method:
        receipts_qs = receipts_qs.filter(method=method)

    paginator = Paginator(receipts_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    filters_querydict = request.GET.copy()
    filters_querydict.pop('page', None)
    filters_querystring = filters_querydict.urlencode()

    stats = receipts_qs.aggregate(stat_total=Sum('total'), stat_tax=Sum('tax_amount'))
    discount_expr = Case(
        When(discount_type='percent', then=F('subtotal') * F('discount') / Decimal('100')),
        default=F('discount'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    stats['stat_discount'] = receipts_qs.aggregate(d=Sum(discount_expr))['d'] or 0

    context = {
        'receipts': page_obj.object_list,
        'page_obj': page_obj,
        'filters_querystring': filters_querystring,
        'filter_method': method,
        'filter_search': search,
        'filters_active': bool(method or search),
        'stat_count': receipts_qs.count(),
        'stat_total': stats['stat_total'] or 0,
        'stat_tax': stats['stat_tax'] or 0,
        'stat_discount': stats['stat_discount'] or 0,
        'methods': Receipt.METHOD_CHOICES,
        'today': timezone.localdate(),
    }
    return render(request, "receipts/list.html", context)

def _next_receipt_no():
    year = timezone.now().year
    prefix = f"RCT-{year}-"
    count = Receipt.objects.filter(receipt_no__startswith=prefix).count()
    number = count + 1
    while True:
        candidate = f"{prefix}{number:04d}"
        if not Receipt.objects.filter(receipt_no=candidate).exists():
            return candidate
        number += 1

def _process_logo(request, receipt=None):
    """Return a stored logo value (data URI) from an optional upload.
    New file → base64 data URI; remove_logo → None; otherwise keep existing."""
    upload = request.FILES.get('logo')
    if upload:
        data = upload.read()
        if len(data) > 512 * 1024:
            raise ValueError("Logo image must be smaller than 512 KB.")
        import base64
        mime = upload.content_type or 'image/png'
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    if request.POST.get('remove_logo') == '1':
        return None
    if receipt is not None:
        return receipt.logo
    return None

def receipt_create_view(request):
    if request.method == "POST":
        member_id = request.POST.get('member_id') or None
        bill_to = request.POST.get('bill_to', '').strip()
        description = request.POST.get('description', '').strip()
        amount = _decimal(request.POST.get('amount'))
        discount_type = request.POST.get('discount_type', 'flat').strip()
        discount_value = _decimal(request.POST.get('discount_value'))
        tax_rate = Financial.get_tax_rate()
        method = request.POST.get('method', 'cash').strip()
        paid_date = _parse_date(request.POST.get('paid_date')) or timezone.localdate()
        notes = request.POST.get('notes', '').strip()

        member = None
        if member_id:
            try:
                member = Member.objects.get(pk=member_id)
            except (Member.DoesNotExist, ValueError):
                return render(request, "receipts/create.html", {
                    'error': "Selected member no longer exists.",
                    'methods': Receipt.METHOD_CHOICES,
                    'today': timezone.localdate(),
                    'next_receipt_no': _next_receipt_no(),
                })

        try:
            logo = _process_logo(request)
        except ValueError as exc:
            return render(request, "receipts/create.html", {
                'error': str(exc),
                'methods': Receipt.METHOD_CHOICES,
                'today': timezone.localdate(),
                'next_receipt_no': _next_receipt_no(),
            })

        receipt_no = request.POST.get('receipt_no', '').strip() or _next_receipt_no()
        if Receipt.objects.filter(receipt_no=receipt_no).exists():
            receipt_no = _next_receipt_no()

        receipt = Receipt(
            receipt_no=receipt_no,
            bill_to=bill_to or None,
            member=member,
            description=description or None,
            subtotal=amount,
            discount_type=discount_type if discount_type in ('flat', 'percent') else 'flat',
            discount=discount_value,
            tax_rate=tax_rate,
            method=method if method in dict(Receipt.METHOD_CHOICES) else 'cash',
            paid_date=paid_date,
            notes=notes or None,
            logo=logo,
        )
        receipt.recalculate()

        try:
            receipt.save()
        except IntegrityError:
            receipt.receipt_no = _next_receipt_no()
            receipt.save()

        return redirect('receipt-detail', pk=receipt.pk)

    context = {
        'methods': Receipt.METHOD_CHOICES,
        'next_receipt_no': _next_receipt_no(),
        'today': timezone.localdate(),
        'settings_tax_rate': Financial.get_tax_rate(),
    }
    return render(request, "receipts/create.html", context)

def receipt_edit_view(request, pk):
    receipt = get_object_or_404(Receipt, pk=pk)

    if request.method == "POST":
        member_id = request.POST.get('member_id') or None
        bill_to = request.POST.get('bill_to', '').strip()
        description = request.POST.get('description', '').strip()
        amount = _decimal(request.POST.get('amount'))
        discount_type = request.POST.get('discount_type', 'flat').strip()
        discount_value = _decimal(request.POST.get('discount_value'))
        tax_rate = Financial.get_tax_rate()
        method = request.POST.get('method', 'cash').strip()
        paid_date = _parse_date(request.POST.get('paid_date')) or timezone.localdate()
        receipt_no = request.POST.get('receipt_no', '').strip() or receipt.receipt_no
        notes = request.POST.get('notes', '').strip()

        member = None
        if member_id:
            try:
                member = Member.objects.get(pk=member_id)
            except (Member.DoesNotExist, ValueError):
                return render(request, "receipts/create.html", {
                    'receipt': receipt,
                    'error': "Selected member no longer exists.",
                })

        try:
            logo = _process_logo(request, receipt)
        except ValueError as exc:
            return render(request, "receipts/create.html", {
                'receipt': receipt,
                'error': str(exc),
            })

        if receipt_no and Receipt.objects.filter(receipt_no=receipt_no).exclude(pk=receipt.pk).exists():
            receipt_no = _next_receipt_no()

        receipt.receipt_no = receipt_no
        receipt.bill_to = bill_to or None
        receipt.member = member
        receipt.description = description or None
        receipt.subtotal = amount
        receipt.discount_type = discount_type if discount_type in ('flat', 'percent') else 'flat'
        receipt.discount = discount_value
        receipt.tax_rate = tax_rate
        receipt.method = method if method in dict(Receipt.METHOD_CHOICES) else 'cash'
        receipt.paid_date = paid_date
        receipt.notes = notes or None
        receipt.logo = logo
        receipt.recalculate()

        try:
            receipt.save()
        except IntegrityError:
            receipt.receipt_no = _next_receipt_no()
            receipt.save()

        return redirect('receipt-detail', pk=receipt.pk)

    context = {
        'receipt': receipt,
        'methods': Receipt.METHOD_CHOICES,
        'next_receipt_no': _next_receipt_no(),
        'today': timezone.localdate(),
        'settings_tax_rate': Financial.get_tax_rate(),
    }
    return render(request, "receipts/create.html", context)

def receipt_detail_view(request, pk):
    receipt = get_object_or_404(Receipt.objects.select_related('member'), pk=pk)
    return render(request, "receipts/detail.html", {'receipt': receipt, 'today': timezone.localdate()})

def receipt_delete_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    receipt = get_object_or_404(Receipt, pk=pk)
    receipt_no = receipt.receipt_no
    receipt.delete()
    return redirect('receipts')

def receipt_pdf_view(request, pk):
    receipt = get_object_or_404(Receipt.objects.select_related('member'), pk=pk)
    try:
        from weasyprint import HTML
        html = render_to_string("receipts/pdf.html", {'receipt': receipt})
        pdf_bytes = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        if request.GET.get('debug'):
            return HttpResponse(
                f"PDF error: {type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
                .replace('\n', '<br>'),
                status=500,
            )
        return HttpResponse(
            "PDF rendering failed. The receipt cannot be exported right now; try again.",
            status=503,
        )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    disposition = "attachment" if request.GET.get('download') == '1' else "inline"
    response['Content-Disposition'] = f'{disposition}; filename="{receipt.receipt_no}.pdf"'
    return response

@login_required_custom
def renewals_view(request):
    return render(request, "renewals/list.html", {"current_user": get_current_user(request)})

def _next_refund_no():
    prefix = "RFD-"
    count = Refund.objects.filter(refund_code__startswith=prefix).count()
    number = count + 1
    while True:
        candidate = f"{prefix}{number:04d}"
        if not Refund.objects.filter(refund_code=candidate).exists():
            return candidate
        number += 1

def _refund_status_badges():
    return Refund.STATUS_CHOICES


@never_cache

@login_required_custom
def refunds_view(request):
    refunds_qs = Refund.objects.select_related('payment', 'member').order_by('-created_at', '-id')

    search = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()

    if search:
        refunds_qs = refunds_qs.filter(
            Q(refund_code__icontains=search)
            | Q(payment__payment_code__icontains=search)
            | Q(member__full_name__icontains=search)
            | Q(reason__icontains=search)
        )
    if status:
        refunds_qs = refunds_qs.filter(status=status)

    pending_count = refunds_qs.filter(status='pending').count()
    approved_total = refunds_qs.filter(status='approved').aggregate(t=Sum('amount'))['t'] or 0
    rejected_total = refunds_qs.filter(status='rejected').aggregate(t=Sum('amount'))['t'] or 0

    paginator = Paginator(refunds_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    filters_querydict = request.GET.copy()
    filters_querydict.pop('page', None)
    filters_querystring = filters_querydict.urlencode()

    context = {
        'refunds': page_obj.object_list,
        'page_obj': page_obj,
        'filters_querystring': filters_querystring,
        'filter_search': search,
        'filter_status': status,
        'filters_active': bool(search or status),
        'stat_count': refunds_qs.count(),
        'stat_pending': pending_count,
        'stat_approved': approved_total,
        'stat_rejected': rejected_total,
        'statuses': Refund.STATUS_CHOICES,
        'today': timezone.localdate(),
    }
    return render(request, "refunds/list.html", context)

@login_required_custom
def attendance_view(request):
    attendance_qs = Attendance.objects.select_related('member').order_by('-date', '-check_in')

    date_from_raw = request.GET.get('date_from', '').strip()
    date_to_raw = request.GET.get('date_to', '').strip()
    status = request.GET.get('status', '').strip()
    member_query = request.GET.get('member', '').strip()

    date_from = _parse_date(date_from_raw)
    date_to = _parse_date(date_to_raw)

    if date_from:
        attendance_qs = attendance_qs.filter(date__gte=date_from)
    if date_to:
        attendance_qs = attendance_qs.filter(date__lte=date_to)
    if status == 'checked_in':
        attendance_qs = attendance_qs.filter(check_out__isnull=True)
    elif status == 'checked_out':
        attendance_qs = attendance_qs.filter(check_out__isnull=False)
    if member_query:
        attendance_qs = attendance_qs.filter(
            Q(member__full_name__icontains=member_query) | Q(member__member_code__icontains=member_query)
        )

    paginator = Paginator(attendance_qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Preserve active filters across Previous/Next links (drop 'page' — the link supplies its own).
    filters_querydict = request.GET.copy()
    filters_querydict.pop('page', None)
    filters_querystring = filters_querydict.urlencode()

    context = {
        'attendance_records': page_obj.object_list,
        'page_obj': page_obj,
        'filters_querystring': filters_querystring,
        'filter_date_from': date_from_raw,
        'filter_date_to': date_to_raw,
        'filter_status': status,
        'filter_member': member_query,
        'filters_active': bool(date_from_raw or date_to_raw or status or member_query),
    }
    return render(request, "attendance/index.html", context)

@login_required_custom
def member_attendance_view(request, pk):
    member = get_object_or_404(Member, pk=pk)
    attendance_qs = Attendance.objects.filter(member=member).order_by('-date')

    paginator = Paginator(attendance_qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'member': member,
        'attendance_records': page_obj.object_list,
        'page_obj': page_obj,
    }
    return render(request, "attendance/member_history.html", context)

@login_required_custom
def expenses_view(request):
    expenses_qs = Expense.objects.all()

    search = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    status = request.GET.get('status', '').strip()
    date_from = _parse_date(request.GET.get('date_from', '').strip())
    date_to = _parse_date(request.GET.get('date_to', '').strip())

    if search:
        expenses_qs = expenses_qs.filter(
            Q(expense_code__icontains=search)
            | Q(description__icontains=search)
            | Q(notes__icontains=search)
        )
    if category:
        expenses_qs = expenses_qs.filter(category=category)
    if status:
        expenses_qs = expenses_qs.filter(status=status)
    if date_from:
        expenses_qs = expenses_qs.filter(expense_date__gte=date_from)
    if date_to:
        expenses_qs = expenses_qs.filter(expense_date__lte=date_to)

    totals = expenses_qs.aggregate(total_amount=Sum('amount'))
    stat_total = totals['total_amount'] or 0
    stat_count = expenses_qs.count()

    pending_total = (
        expenses_qs.filter(status='pending').aggregate(t=Sum('amount'))['t'] or 0
    )
    cleared_total = (
        expenses_qs.filter(status='cleared').aggregate(t=Sum('amount'))['t'] or 0
    )

    paginator = Paginator(expenses_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    filters_querydict = request.GET.copy()
    filters_querydict.pop('page', None)
    filters_querystring = filters_querydict.urlencode()

    context = {
        'expenses': page_obj.object_list,
        'page_obj': page_obj,
        'filters_querystring': filters_querystring,
        'filter_search': search,
        'filter_category': category,
        'filter_status': status,
        'filter_date_from': date_from,
        'filter_date_to': date_to,
        'filters_active': bool(search or category or status or date_from or date_to),
        'stat_total': stat_total,
        'stat_count': stat_count,
        'stat_pending': pending_total,
        'stat_cleared': cleared_total,
        'categories': Expense.CATEGORY_CHOICES,
        'statuses': Expense.STATUS_CHOICES,
        'today': timezone.localdate(),
    }
    return render(request, "expenses/list.html", context)

@never_cache

@login_required_custom
def notifications_view(request):
    all_notifs = get_notifications()

    category = request.GET.get('category', '').strip()
    q = request.GET.get('q', '').strip().lower()

    if category and category != 'all':
        all_notifs = [n for n in all_notifs if n.ntype == category]
    if q:
        all_notifs = [n for n in all_notifs
                      if q in n.title.lower() or q in n.message.lower()]

    def cat_count(ntype):
        return sum(1 for n in all_notifs if n.ntype == ntype) if not category and not q else None

    paginator = Paginator(all_notifs, 12)
    page_obj = paginator.get_page(request.GET.get('page'))

    unread = sum(1 for n in all_notifs if not n.read)

    filters_querydict = request.GET.copy()
    filters_querydict.pop('page', None)
    filters_querydict.pop('category', None)
    filters_querystring = filters_querydict.urlencode()

    return render(request, "notifications/index.html", {
        'notifications': page_obj.object_list,
        'page_obj': page_obj,
        'filters_querystring': filters_querystring,
        'filter_category': category,
        'filter_q': request.GET.get('q', ''),
        'filters_active': bool(category and category != 'all' or q),
        'total_count': len(all_notifs),
        'unread_count': unread,
        'cat_expiration': sum(1 for n in all_notifs if n.ntype == 'expiration'),
        'cat_renewal': sum(1 for n in all_notifs if n.ntype == 'renewal'),
        'cat_expired': sum(1 for n in all_notifs if n.ntype == 'expired'),
        'cat_payment': sum(1 for n in all_notifs if n.ntype == 'payment'),
        'cat_refund': sum(1 for n in all_notifs if n.ntype == 'refund'),
        'settings': NotificationSetting.objects.all(),
    })


@never_cache

def notification_mark_read_view(request, nkey):
    NotificationRead.objects.get_or_create(nkey=nkey)
    return redirect('notifications')


@never_cache

def notification_mark_all_read_view(request):
    for n in get_notifications():
        if not n.read:
            NotificationRead.objects.get_or_create(nkey=n.key)
    return redirect('notifications')

@login_required_custom
def reports_view(request):
    return render(request, "reports/index.html")

def _safe_user_profiles(*args, **kwargs):
    try:
        # Evaluate immediately to catch missing table
        return list(UserProfile.objects.select_related('user').order_by('user__full_name')[:100])
    except Exception:
        return []

def users_view(request):
    # Enhanced from users-backend: show counts, but using dev's User model
    # Safe handling if user_profiles table doesn't exist yet
    user_profiles = _safe_user_profiles(request)
    # If table missing/empty, synthesize from dev Users so names appear
    if not user_profiles:
        try:
            all_users = list(User.objects.all().order_by('full_name')[:100])
            # Build pseudo-profiles compatible with template (profile.user.first_name etc)
            pseudo = []
            for u in all_users:
                p = type('PseudoProfile', (), {})()
                p.id = u.id
                p.role = (u.role or "").lower()
                p.user = u
                # Ensure user has required attrs (added to model, but keep fallback)
                if not hasattr(u, 'get_full_name'):
                    u.get_full_name = lambda s=u: s.full_name
                pseudo.append(p)
            if pseudo:
                user_profiles = pseudo
        except Exception:
            pass
    try:
        total_active = User.objects.filter(status='Active').count()
        admin_count = User.objects.filter(role='Admin').count()
        accountant_count = User.objects.filter(role='Accountant').count()
        try:
            staff_count = UserProfile.objects.filter(role=UserProfile.ROLE_STAFF).count()
        except Exception:
            # Fallback count staff from User role if profile table missing
            try:
                staff_count = User.objects.filter(role='Staff').count()
            except Exception:
                staff_count = 0
        try:
            all_users = list(User.objects.all().order_by('full_name')[:100])
        except Exception:
            all_users = []
        context = {
            'current_user': get_current_user(request),
            'user_profiles': user_profiles,
            'users': all_users,
            'total_active': total_active,
            'admin_count': admin_count,
            'accountant_count': accountant_count,
            'staff_count': staff_count,
        }
    except Exception as e:
        context = {'current_user': get_current_user(request), 'user_profiles': user_profiles or [], 'users': []}
    return render(request, "users/list.html", context)

def _get_admin_user():
    """Helper compatible with current User model (CharField avatar, no get_admin)."""
    admin, created = User.objects.get_or_create(
        pk=1,
        defaults={
            "full_name": "Admin",
            "email": "admin@ironcore.gym",
            "username": "admin",
            "password": make_password("admin123"),
            "role": "Admin",
            "status": "Active",
        },
    )
    return admin


def _avatar_url(user):
    """Return avatar URL / string compat for CharField vs ImageField."""
    if not getattr(user, "avatar", None):
        return ""
    av = user.avatar
    # ImageField has .url, CharField is plain string
    try:
        return av.url  # type: ignore
    except Exception:
        return str(av)


@login_required_custom
def settings_view(request):
    business = BusinessInformation.get_singleton()
    financial = FinancialSetting.get_singleton()
    payment_methods = PaymentMethod.get_all()
    notification_settings = NotificationSetting.get_all()
    admin_user = _get_admin_user()
    response = render(request, "settings/index.html", {
        "current_user": get_current_user(request),
        "business": business,
        "financial": financial,
        "payment_methods": payment_methods,
        "notification_settings": notification_settings,
        "admin_user": admin_user,
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
        return JsonResponse({"ok": False, "error": "Tax rate must be a number between 0 and 100."}, status=400)
    if tax_float < 0 or tax_float > 100:
        return JsonResponse({"ok": False, "error": "Tax rate must be between 0 and 100."}, status=400)
    financial.currency = "USD"
    financial.tax_rate = Decimal(str(tax_float))
    financial.save()
    return JsonResponse({"ok": True, "currency": financial.currency, "tax_rate": str(financial.tax_rate)})


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
        return JsonResponse({"methods": [{"code": m.code, "name": m.name, "enabled": m.enabled} for m in PaymentMethod.get_all()]})
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)
    methods = PaymentMethod.get_all()
    next_enabled = {m.code: bool(payload.get(m.code, False)) for m in methods}
    if not any(next_enabled.values()):
        return JsonResponse({"ok": False, "error": "At least one payment method must be enabled."}, status=400)
    for m in methods:
        if m.enabled != next_enabled[m.code]:
            m.enabled = next_enabled[m.code]
            m.save()
    return JsonResponse({"ok": True, "methods": [{"code": m.code, "name": m.name, "enabled": m.enabled} for m in methods]})


@require_http_methods(["GET", "POST"])
def notification_settings_api(request):
    if request.method == "GET":
        return JsonResponse({"settings": [{"code": n.code, "name": n.name, "enabled": n.enabled} for n in NotificationSetting.get_all()]})
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)
    for n in NotificationSetting.get_all():
        if n.code in payload:
            n.enabled = bool(payload[n.code])
            n.save()
    return JsonResponse({"ok": True, "settings": [{"code": n.code, "name": n.name, "enabled": n.enabled} for n in NotificationSetting.get_all()]})


@require_http_methods(["GET", "POST"])
def admin_profile_settings_api(request):
    admin = _get_admin_user()
    if request.method == "GET":
        return JsonResponse({
            "full_name": admin.full_name,
            "email": admin.email,
            "username": admin.username,
            "role": admin.role,
            "status": admin.status,
            "avatar": _avatar_url(admin),
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
        # Save avatar directly to DB as base64 data URI (no localhost file)
        try:
            import base64
            f = request.FILES["avatar"]
            # Validate
            if f.content_type not in ("image/png", "image/jpeg", "image/jpg", "image/webp"):
                # Still allow but store
                pass
            if f.size > 2 * 1024 * 1024:
                return JsonResponse({"ok": False, "error": "Avatar must be under 2MB."}, status=400)
            data = f.read()
            b64 = base64.b64encode(data).decode('utf-8')
            mime = f.content_type or "image/png"
            admin.avatar = f"data:{mime};base64,{b64}"
        except Exception as e:
            return JsonResponse({"ok": False, "error": f"Failed to process avatar: {e}"}, status=400)
    admin.save()
    return JsonResponse({
        "ok": True,
        "full_name": admin.full_name,
        "email": admin.email,
        "username": admin.username,
        "role": admin.role,
        "status": admin.status,
        "avatar": _avatar_url(admin),
    })


@login_required_custom
def member_detail_view_legacy(request):
    today = date.today()
    Subscription.objects.filter(status__in=["active", "expiring"], end_date__lt=today).update(status="suspended")
    code = request.GET.get("code") or request.GET.get("subscription_code")
    sub_id = request.GET.get("id")
    member_name = request.GET.get("member")
    sub = None
    if code:
        try:
            sub = Subscription.objects.select_related("member", "plan").get(subscription_code=code)
        except Subscription.DoesNotExist:
            sub = None
    elif sub_id:
        try:
            sub = Subscription.objects.select_related("member", "plan").get(id=sub_id)
        except Subscription.DoesNotExist:
            sub = None
    elif member_name:
        sub = Subscription.objects.select_related("member", "plan").filter(member__full_name__icontains=member_name).first()
    else:
        sub = Subscription.objects.select_related("member", "plan").order_by("-created_at").first()
    return render(request, "members/detail.html", {"current_user": get_current_user(request), "subscription": sub, "member": None})

@login_required_custom
def member_detail_view(request, pk):
    member = get_object_or_404(Member, pk=pk)
    subscriptions = Subscription.objects.filter(member=member).order_by('-start_date')
    payments = Payment.objects.filter(member=member).order_by('-paid_at')
    # Cheap aggregate over the same already-scoped queryset — not a separate
    # "outstanding" figure, since that's just Member.balance, already shown
    # in the summary card above the tabs.
    total_paid = payments.filter(status='success').aggregate(total=Sum('total'))['total'] or 0
    # This is a tab on a detail page, not a standalone list — full Paginator
    # controls would be overkill here. A member's attendance realistically
    # tops out in the low hundreds even after years of daily visits, so a
    # simple "most recent 20" cap (matching the page size used elsewhere in
    # this project) keeps the tab fast and readable without needing Previous/
    # Next controls crammed into a tab panel.
    attendance_records = Attendance.objects.filter(member=member).order_by('-date')[:20]

    # Statement = subscriptions + payments merged into one chronological feed.
    # No extra queries — reuse the querysets already fetched above. Each entry
    # becomes a plain dict with a common 'date' key (a date, not datetime — a
    # payment's paid_at is a datetime, a subscription's start_date is a plain
    # date, so payments are normalized down to .date() for sorting alongside
    # subscriptions) plus a 'type' tag the template branches on, then the
    # combined list is sorted by that common key, most recent first.
    statement_entries = []
    for sub in subscriptions:
        statement_entries.append({
            'type': 'subscription',
            'date': sub.start_date,
            'code': sub.subscription_code or f"#{sub.pk}",
            'status': sub.get_status_display() if sub.status else None,
            'amount': None,
        })
    for payment in payments:
        statement_entries.append({
            'type': 'payment',
            'date': payment.paid_at.date() if payment.paid_at else None,
            'code': payment.payment_code or f"#{payment.pk}",
            'method': payment.get_method_display() if payment.method else None,
            'status': payment.get_status_display() if payment.status else None,
            'amount': payment.total,
        })
    statement_entries.sort(key=lambda entry: entry['date'] or datetime.date.min, reverse=True)

    return render(request, "members/detail.html", {
        "member": member,
        "subscriptions": subscriptions,
        "payments": payments,
        "total_paid": total_paid,
        "attendance_records": attendance_records,
        "statement_entries": statement_entries,
    })

def _create_member(data, attempt=1):
    """Insert a Member with a freshly generated member_code.

    Raw INSERT (not Member.objects.create()) so we only supply the columns the
    form actually collects — balance/initials are left out entirely so Postgres
    applies its own column defaults (0.00 / NULL) instead of Django writing
    explicit NULLs for fields we never touched.

    member_code is an app-level generated sequence (see MemberForm.generate_member_code),
    not an atomic DB one, so two concurrent submissions could compute the same next
    code. On a UNIQUE violation, retry once with a freshly regenerated code before
    giving up — same pattern used for the Attendance check-in race.
    """
    member_code = MemberForm.generate_member_code()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO members (member_code, full_name, phone, email, join_date, status) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                [member_code, data['full_name'], data['phone'], data['email'] or None, data['join_date'], data['status']],
            )
            new_id = cursor.fetchone()[0]
        return Member.objects.get(pk=new_id)
    except IntegrityError:
        if attempt >= 2:
            return None
        return _create_member(data, attempt=attempt + 1)

def _update_member(member, data):
    """UPDATE an existing Member's editable fields — member_code is left untouched.

    Same raw-SQL reasoning as _create_member: only touch the columns this form
    actually collects, so balance/initials are never overwritten by this path.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE members SET full_name = %s, phone = %s, email = %s, join_date = %s, status = %s WHERE id = %s",
            [data['full_name'], data['phone'], data['email'] or None, data['join_date'], data['status'], member.pk],
        )
    return Member.objects.get(pk=member.pk)

@login_required_custom
def member_add_view(request, pk=None):
    member = None
    if pk is not None:
        member = get_object_or_404(Member, pk=pk)

    if request.method == "POST":
        form = MemberForm(request.POST, instance_member=member)
        if form.is_valid():
            if member is not None:
                updated = _update_member(member, form.cleaned_data)
                messages.success(request, f'Member "{updated.full_name}" was updated successfully.')
            else:
                created = _create_member(form.cleaned_data)
                if created is None:
                    messages.error(request, "Could not save this member due to a conflict — please try again.")
                    return render(request, "members/add.html", {"form": form, "is_edit": False})
                messages.success(request, f'Member "{created.full_name}" was added successfully ({created.member_code}).')
            return redirect('members')
    else:
        initial = None
        if member is not None:
            initial = {
                'full_name': member.full_name,
                'phone': member.phone,
                'email': member.email,
                # ISO string, not the raw date object — an unbound form's
                # BoundField.value() renders a date object through Django's
                # locale-aware date filter ("June 1, 2024"), which isn't a
                # valid value for <input type="date"> and silently fails to
                # pre-fill in the browser.
                'join_date': member.join_date.isoformat() if member.join_date else '',
                'status': member.status,
            }
        form = MemberForm(initial=initial, instance_member=member)

    return render(request, "members/add.html", {
        "form": form,
        "is_edit": member is not None,
        "edit_member": member,
    })

@login_required_custom
def member_delete_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    member = get_object_or_404(Member, pk=pk)

    # attendance/subscriptions/payments/member_services all have ON DELETE CASCADE
    # to members in the live schema (confirmed via pg_constraint) — a hard DELETE
    # there wouldn't raise anything to catch, it would just silently wipe that
    # member's real attendance and financial history. Pre-check those specific
    # tables and refuse the delete if any rows exist, rather than relying on an
    # IntegrityError that CASCADE will never actually raise.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT "
            "(SELECT count(*) FROM attendance WHERE member_id = %s) + "
            "(SELECT count(*) FROM subscriptions WHERE member_id = %s) + "
            "(SELECT count(*) FROM payments WHERE member_id = %s) + "
            "(SELECT count(*) FROM member_services WHERE member_id = %s)",
            [member.pk, member.pk, member.pk, member.pk],
        )
        related_count = cursor.fetchone()[0]

    if related_count > 0:
        messages.error(
            request,
            f'Cannot delete "{member.full_name}" — this member has existing attendance, '
            'subscription, or payment records. Set their status to Suspended instead if '
            'you need to disable them without losing that history.',
        )
        return redirect('members')

    try:
        member.delete()
    except IntegrityError:
        # Safety net for invoices/refunds, which use ON DELETE NO ACTION (i.e.
        # Postgres itself blocks the delete with a real FK violation when rows
        # exist there) — the pre-check above doesn't cover these since the DB
        # already protects them; this just turns that into a clean message
        # instead of a 500.
        messages.error(
            request,
            f'Cannot delete "{member.full_name}" — this member has existing payment/attendance records.',
        )
        return redirect('members')

    messages.success(request, f'Member "{member.full_name}" was deleted.')
    return redirect('members')

@login_required_custom
def services_view(request):
    services_qs = Service.objects.all().order_by('name')
    paginator = Paginator(services_qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    context = {
        'services': page_obj.object_list,
        'page_obj': page_obj,
    }
    return render(request, "services/list.html", context)

def _create_service(data, attempt=1):
    """Insert a Service with a freshly generated service_code.

    Raw INSERT (not Service.objects.create()) — no GENERATED-column concern
    here, but kept consistent with the raw-INSERT pattern used for
    Member/Plan so only the real columns this form collects are ever touched.

    service_code is an app-level generated sequence (see
    ServiceForm.generate_service_code), not an atomic DB one, so retry once on
    a UNIQUE violation before giving up — same pattern as Member/Plan.
    """
    service_code = ServiceForm.generate_service_code()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO services (service_code, name, description, is_active) VALUES (%s, %s, %s, %s) RETURNING id",
                [service_code, data['name'], data['description'] or None, data['is_active']],
            )
            new_id = cursor.fetchone()[0]
        return Service.objects.get(pk=new_id)
    except IntegrityError:
        if attempt >= 2:
            return None
        return _create_service(data, attempt=attempt + 1)

def service_add_view(request):
    if request.method == "POST":
        form = ServiceForm(request.POST)
        if form.is_valid():
            service = _create_service(form.cleaned_data)
            if service is None:
                messages.error(request, "Could not save this service due to a conflict — please try again.")
                return render(request, "services/add.html", {"form": form})

            messages.success(request, f'Service "{service.name}" was added successfully ({service.service_code}).')
            return redirect('services')
    else:
        form = ServiceForm()

    return render(request, "services/add.html", {"form": form})

# The frontend's Duration dropdown only offers these buckets, not a raw day
# count — approximated as flat day-counts since a plan *template* has no
# anchor start date for genuine calendar-month arithmetic (that only applies
# once a real Subscription exists with a start_date). 12 months is treated
# as a full year (365 days) rather than 12*30=360, matching how an "annual"
# plan is commonly billed elsewhere.
PLAN_DURATION_DAYS_BY_BUCKET = {
    '1_month': 30,
    '3_months': 90,
    '6_months': 180,
    '12_months': 365,
}

def _plan_service_context(plan):
    """Assigned vs. available Service records for the Services/Benefits section
    on the Edit Plan page — only meaningful once a plan actually exists (has a
    pk), so callers only include this when is_edit is True.
    """
    if plan is None:
        return {}
    assigned_services = Service.objects.filter(plan_services__plan=plan).order_by('name')
    available_services = Service.objects.exclude(
        id__in=assigned_services.values_list('id', flat=True)
    ).order_by('name')
    return {
        'assigned_services': assigned_services,
        'available_services': available_services,
    }

def plan_assign_service_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    plan = get_object_or_404(MembershipPlan, pk=pk)
    # service_id comes from the POST body (a single dropdown of available
    # services), not the URL — unlike unassign below, there's no single
    # "which service" implied by the page itself, since the add control has
    # to let the admin pick from N available ones.
    service_id = request.POST.get('service_id')
    if not service_id:
        messages.error(request, "Select a service to add.")
        return redirect('plan-edit', pk=plan.pk)
    service = get_object_or_404(Service, pk=service_id)

    try:
        _, created = PlanService.objects.get_or_create(plan=plan, service=service)
    except IntegrityError:
        # Lost a race with a concurrent assign of the same plan+service pair.
        created = False

    if created:
        messages.success(request, f'"{service.name}" was added to "{plan.name}".')
    else:
        messages.info(request, f'"{service.name}" is already assigned to "{plan.name}".')

    return redirect('plan-edit', pk=plan.pk)

def plan_unassign_service_view(request, pk, service_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    plan = get_object_or_404(MembershipPlan, pk=pk)
    service = get_object_or_404(Service, pk=service_pk)

    deleted_count, _ = PlanService.objects.filter(plan=plan, service=service).delete()
    if deleted_count:
        messages.success(request, f'"{service.name}" was removed from "{plan.name}".')
    else:
        messages.info(request, f'"{service.name}" was not assigned to "{plan.name}".')

    return redirect('plan-edit', pk=plan.pk)

@login_required_custom
def plan_create_view(request, pk=None):
    plan = None
    if pk is not None:
        plan = get_object_or_404(MembershipPlan, pk=pk)

    if request.method == "POST":
        duration_bucket = request.POST.get('duration', '')

        if duration_bucket == 'custom':
            # Custom-duration plans are a separate flow (templates/plans/custom.html,
            # its own sibling task) with a different set of fields entirely
            # (services checkboxes, etc.) — this form can't represent one, so
            # send the admin to the flow that actually can rather than showing
            # a dead-end validation error. Applies the same way whether adding
            # or editing — "convert this plan to custom" isn't something this
            # form can do either.
            messages.info(
                request,
                'This form is for standard, fixed-duration plans. Use "Create Custom Plan" for custom/negotiated tiers.',
            )
            return redirect('plan-custom')

        post_data = request.POST.copy()
        if duration_bucket == '__current__' and plan is not None:
            # Editing a plan whose duration_days doesn't match any preset
            # bucket (see the GET branch below) — leave it exactly as-is
            # rather than run it through the bucket map (which would resolve
            # to '' and fail validation), silently overwriting a value that
            # was presumably set deliberately outside this form.
            post_data['duration_days'] = str(plan.duration_days)
        else:
            post_data['duration_days'] = str(PLAN_DURATION_DAYS_BY_BUCKET.get(duration_bucket, ''))
        # This view only ever handles standard (non-custom) plans — force
        # is_fixed True regardless of the checkbox's absence from this
        # template, rather than let an unchecked/missing checkbox silently
        # default to False.
        post_data['is_fixed'] = 'on'

        form = PlanForm(post_data, instance_plan=plan)
        if form.is_valid():
            data = form.cleaned_data

            if plan is not None:
                # Only regenerate the slug if the name actually changed.
                # instance_plan is passed to generate_slug() either way so its
                # collision check correctly excludes the plan's own current
                # slug (otherwise re-saving a plan with an unchanged name
                # would see its own slug as "taken" and bump it to "-2").
                slug = plan.slug if data['name'] == plan.name else PlanForm.generate_slug(data['name'], instance_plan=plan)
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "UPDATE membership_plans SET name = %s, slug = %s, duration_days = %s, "
                            "price = %s, is_fixed = %s, is_active = %s WHERE id = %s",
                            [data['name'], slug, data['duration_days'], data['price'], data['is_fixed'], data['status'] == 'active', plan.pk],
                        )
                except IntegrityError:
                    messages.error(request, "Could not save this plan due to a conflict — please try again.")
                    return render(request, "plans/create.html", {
                        "form": form,
                        "submitted_duration": duration_bucket,
                        "is_edit": True,
                        "edit_plan": plan,
                        "current_duration_days": plan.duration_days,
                        **_plan_service_context(plan),
                    })

                updated = MembershipPlan.objects.get(pk=plan.pk)
                messages.success(request, f'Membership plan "{updated.name}" was updated successfully.')
                return redirect('plans')

            # Add path (no pk) — unchanged from SMM2-151.
            slug = PlanForm.generate_slug(data['name'])
            try:
                # Raw INSERT naming only the real columns this form collects —
                # same reasoning as Member/Attendance elsewhere in this
                # project: leaving created_at out entirely lets Postgres apply
                # its own now() default instead of Django writing an explicit
                # NULL for a column we never touched.
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO membership_plans (name, slug, duration_days, price, is_fixed, is_active) "
                        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                        [data['name'], slug, data['duration_days'], data['price'], data['is_fixed'], data['status'] == 'active'],
                    )
                    new_id = cursor.fetchone()[0]
            except IntegrityError:
                messages.error(request, "Could not save this plan due to a conflict — please try again.")
                return render(request, "plans/create.html", {"form": form, "submitted_duration": duration_bucket, "is_edit": False})

            created = MembershipPlan.objects.get(pk=new_id)
            messages.success(request, f'Membership plan "{created.name}" was created successfully.')
            return redirect('plans')

        return render(request, "plans/create.html", {
            "form": form,
            "submitted_duration": duration_bucket,
            "is_edit": plan is not None,
            "edit_plan": plan,
            "current_duration_days": plan.duration_days if plan is not None else None,
            **_plan_service_context(plan),
        })

    # GET
    initial = None
    submitted_duration = ""
    current_duration_days = None
    if plan is not None:
        reverse_bucket_map = {days: bucket for bucket, days in PLAN_DURATION_DAYS_BY_BUCKET.items()}
        submitted_duration = reverse_bucket_map.get(plan.duration_days, '__current__')
        if submitted_duration == '__current__':
            current_duration_days = plan.duration_days
        initial = {
            'name': plan.name,
            'price': plan.price,
            'status': 'active' if plan.is_active else 'inactive',
        }
    form = PlanForm(initial=initial, instance_plan=plan)
    return render(request, "plans/create.html", {
        "form": form,
        "submitted_duration": submitted_duration,
        "is_edit": plan is not None,
        "edit_plan": plan,
        "current_duration_days": current_duration_days,
        **_plan_service_context(plan),
    })

@login_required_custom
def plan_custom_view(request):
    if request.method == "POST":
        duration_bucket = request.POST.get('duration', '')
        post_data = request.POST.copy()

        # Unlike the standard Create Plan flow, "custom" duration here has
        # nowhere further to redirect to — this page IS the custom-plan flow.
        # The existing template had no real day-count input backing that
        # option, so I added one (custom_duration_days, required only in this
        # branch) rather than silently picking an arbitrary default. Leaving
        # it blank simply fails PlanForm's normal "required" validation on
        # duration_days.
        if duration_bucket == 'custom':
            post_data['duration_days'] = post_data.get('custom_duration_days', '')
        else:
            post_data['duration_days'] = str(PLAN_DURATION_DAYS_BY_BUCKET.get(duration_bucket, ''))

        # Template field is "custom_plan_name", not "name" — map it onto the
        # PlanForm field it actually corresponds to.
        post_data['name'] = post_data.get('custom_plan_name', '')

        # Deliberately do NOT set post_data['is_fixed'] here (unlike the
        # standard Create Plan view, which forces it to 'on'). This template
        # has no is_fixed checkbox either, so PlanForm's
        # BooleanField(required=False) resolves its absence to False — the
        # correct value for a custom plan, the explicit non-fixed counterpart
        # to the standard flow.

        # The 10 "services" checkboxes on this page are read here but there is
        # no MembershipPlan column to store them in yet — that's SMM2-156+
        # (Services/Benefits) territory. They are intentionally NOT persisted
        # anywhere; nothing selected here survives this request.

        form = PlanForm(post_data)
        if form.is_valid():
            data = form.cleaned_data
            slug = PlanForm.generate_slug(data['name'])
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO membership_plans (name, slug, duration_days, price, is_fixed, is_active) "
                        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                        [data['name'], slug, data['duration_days'], data['price'], data['is_fixed'], data['status'] == 'active'],
                    )
                    new_id = cursor.fetchone()[0]
            except IntegrityError:
                messages.error(request, "Could not save this custom plan due to a conflict — please try again.")
                return render(request, "plans/custom.html", {"form": form, "submitted_duration": duration_bucket})

            plan = MembershipPlan.objects.get(pk=new_id)
            messages.success(request, f'Custom plan "{plan.name}" was created successfully.')
            return redirect('plans')

        return render(request, "plans/custom.html", {"form": form, "submitted_duration": duration_bucket})

    form = PlanForm()
    return render(request, "plans/custom.html", {"form": form, "submitted_duration": ""})

@login_required_custom
def subscription_detail_view(request):
    # auto-suspend expired before fetching detail too
    today = date.today()
    Subscription.objects.filter(status__in=["active", "expiring"], end_date__lt=today).update(status="suspended")
    code = request.GET.get("code") or request.GET.get("subscription_code")
    sub_id = request.GET.get("id")
    member_name = request.GET.get("member")
    sub = None
    if code:
        try:
            sub = Subscription.objects.select_related("member", "plan").get(subscription_code=code)
        except Subscription.DoesNotExist:
            sub = None
    elif sub_id:
        try:
            sub = Subscription.objects.select_related("member", "plan").get(id=sub_id)
        except Subscription.DoesNotExist:
            sub = None
    elif member_name:
        sub = Subscription.objects.select_related("member", "plan").filter(member__full_name__icontains=member_name).first()
    else:
        # fallback to latest
        sub = Subscription.objects.select_related("member", "plan").order_by("-created_at").first()

    return render(request, "subscriptions/detail.html", {"current_user": get_current_user(request), "subscription": sub})

@login_required_custom
def subscription_create_view(request):
    members = Member.objects.all().order_by("full_name")
    standard_plans = MembershipPlan.objects.filter(is_active=True).order_by("price")[:6]
    # for pricing calculation without tax, we just need base - discount

    if request.method == "POST":
        # Check if this is a JSON fetch or normal form POST
        # Our wizard uses hidden fields + form POST, but we can handle both
        member_select = request.POST.get("member_select", "").strip()
        standard_plan = request.POST.get("standard_plan", "").strip()
        custom_duration = request.POST.get("custom_duration", "").strip()
        custom_price = request.POST.get("custom_price", "").strip()
        custom_benefits = request.POST.get("custom_benefits", "").strip()
        start_date_str = request.POST.get("start_date", "").strip()
        end_date_str = request.POST.get("end_date", "").strip()
        base_price_str = request.POST.get("base_price", "").strip()
        discount_str = request.POST.get("discount", "").strip() or "0"
        payment_method = request.POST.get("payment_method", "").strip()
        confirm_payment = request.POST.get("confirm_payment")

        errors = {}

        # --- Member ---
        member = None
        if not member_select:
            errors["member"] = "Please select a member."
        else:
            # value is "MBR-9921A|Marcus Thorne" or "MBR-...|Name" with id|name|...
            # try to extract member_code (first part)
            code = member_select.split("|")[0].strip()
            try:
                member = Member.objects.get(member_code=code)
            except Member.DoesNotExist:
                # fallback try data-member-id if sent via JS? check member_id field
                member_id = request.POST.get("member_id")
                if member_id:
                    try:
                        member = Member.objects.get(id=member_id)
                    except:
                        errors["member"] = "Selected member not found."
                else:
                    errors["member"] = "Selected member not found."

        # --- Plan ---
        plan = None
        plan_mode = "standard"
        # detect if standard_plan is empty and custom fields filled -> custom
        if standard_plan:
            # standard_plan is "id|name|price"
            try:
                plan_id = int(standard_plan.split("|")[0])
                plan = MembershipPlan.objects.get(id=plan_id)
            except (ValueError, MembershipPlan.DoesNotExist, IndexError):
                errors["standard_plan"] = "Invalid plan selected."
        else:
            # check if custom plan attempted
            if custom_duration or custom_price or custom_benefits:
                plan_mode = "custom"
                if not custom_duration:
                    errors["custom_duration"] = "Duration required."
                if not custom_price:
                    errors["custom_price"] = "Price required."
                else:
                    try:
                        cp = Decimal(custom_price)
                        if cp <= 0:
                            errors["custom_price"] = "Price must be > 0."
                    except (InvalidOperation, ValueError):
                        errors["custom_price"] = "Invalid price."
                if not custom_benefits:
                    errors["custom_benefits"] = "Benefits required."
                # if no errors, create custom plan on the fly
                if not errors:
                    duration_map = {
                        "1_month": 30,
                        "3_months": 90,
                        "6_months": 180,
                        "12_months": 365,
                        "custom": 30,
                    }
                    days = duration_map.get(custom_duration, 30)
                    # create custom plan
                    custom_name = f"Custom ({custom_benefits.split(chr(10))[0][:30]})"
                    try:
                        with transaction.atomic():
                            plan = MembershipPlan.objects.create(
                                name=custom_name[:100],
                                slug=f"custom-{timezone.now().timestamp():.0f}",
                                duration_days=days,
                                price=Decimal(custom_price),
                                is_fixed=False,
                                is_active=True,
                            )
                    except Exception as e:
                        errors["custom_price"] = f"Failed to create custom plan: {e}"
            else:
                errors["standard_plan"] = "Please select a plan."

        # --- Dates ---
        start_date = None
        end_date = None
        if not start_date_str:
            errors["start_date"] = "Start date required."
        else:
            try:
                start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
            except ValueError:
                errors["start_date"] = "Invalid start date."
        if not end_date_str:
            errors["end_date"] = "End date required."
        else:
            try:
                end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except ValueError:
                errors["end_date"] = "Invalid end date."
        if start_date and end_date and end_date <= start_date:
            errors["end_date"] = "End date must be after start date."

        # --- Pricing (no tax) ---
        base_price = None
        discount = Decimal("0")
        try:
            base_price = Decimal(base_price_str)
            if base_price <= 0:
                errors["base_price"] = "Base price must be > 0."
        except (InvalidOperation, ValueError, TypeError):
            if base_price_str == "":
                # fallback to plan price if not provided
                if plan:
                    base_price = plan.price
                else:
                    errors["base_price"] = "Base price required."
            else:
                errors["base_price"] = "Invalid base price."

        try:
            discount = Decimal(discount_str) if discount_str else Decimal("0")
            if discount < 0:
                errors["discount"] = "Discount cannot be negative."
            if base_price and discount > base_price:
                errors["discount"] = "Discount cannot exceed base price."
        except (InvalidOperation, ValueError):
            errors["discount"] = "Invalid discount."

        # --- Payment ---
        if not payment_method:
            errors["payment_method"] = "Payment method required."
        elif payment_method not in ["cash", "card", "bank_transfer", "online"]:
            errors["payment_method"] = "Invalid payment method."

        if not confirm_payment:
            errors["confirm_payment"] = "You must confirm payment."

        # if errors, re-render with messages
        if errors:
            for field, msg in errors.items():
                messages.error(request, f"{field}: {msg}")
            return render(request, "subscriptions/create.html", {
                "current_user": get_current_user(request),
                "members": members,
                "standard_plans": standard_plans,
                "errors": errors,
                "form_data": request.POST,
            })

        # --- Create subscription ---
        try:
            with transaction.atomic():
                count = Subscription.objects.count() + 1
                code = f"SUB-{member.member_code}-{count:04d}"
                while Subscription.objects.filter(subscription_code=code).exists():
                    count += 1
                    code = f"SUB-{member.member_code}-{count:04d}"

                final_total = base_price - discount  # no tax

                sub = Subscription.objects.create(
                    subscription_code=code,
                    member=member,
                    plan=plan,
                    start_date=start_date,
                    end_date=end_date,
                    status="active",
                    auto_renew=True,
                )

            # Payment audit outside main transaction to avoid rollback on failure
            try:
                from django.db import connection
                with connection.cursor() as cur:
                    short_code = f"{sub.id:04d}"
                    cur.execute(
                        "INSERT INTO payments (payment_code, receipt_no, member_id, subscription_id, amount, discount, total, method, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [f"PAY-{short_code}", f"RCPT-{short_code}", member.id, sub.id, str(base_price), str(discount), str(final_total), payment_method, "success"]
                    )
            except Exception:
                pass
            # invalidate metrics and page cache for subscriptions
            cache.delete("sub_metrics")
            # clear per-user page cache for list and dashboard
            uid = request.session.get("user_id")
            cache.delete(f"page:subscriptions_view:{uid}:/subscriptions/")
            cache.delete(f"page:dashboard_view:{uid}:/dashboard/")

            messages.success(request, f"Subscription {code} created successfully for {member.full_name} (${final_total:.2f})")
            # If request is AJAX, return JSON
            if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
                from django.http import JsonResponse
                return JsonResponse({"success": True, "code": code, "total": str(final_total), "redirect": "/subscriptions/"})
            return redirect("subscriptions")
        except Exception as e:
            messages.error(request, f"Failed to create subscription: {e}")
            return render(request, "subscriptions/create.html", {
                "current_user": get_current_user(request),
                "members": members,
                "standard_plans": standard_plans,
                "errors": {"__all__": str(e)},
                "form_data": request.POST,
            })

    # GET
    return render(request, "subscriptions/create.html", {
        "current_user": get_current_user(request),
        "members": members,
        "standard_plans": standard_plans,
    })

@login_required_custom
@login_required_custom
def payment_detail_view(request):
    # Support /payments/detail/?id=1 or ?code=PAY-0001 or ?receipt=RCPT-0001
    pk = request.GET.get('id') or request.GET.get('pk') or request.POST.get('id')
    code = request.GET.get('code') or request.GET.get('payment_code')
    receipt = request.GET.get('receipt') or request.GET.get('receipt_no')
    # Allow POST with id in URL query or hidden field
    if request.method == "POST" and not pk:
        pk = request.POST.get('payment_id') or request.POST.get('id')
    payment = None
    try:
        if pk:
            payment = Payment.objects.select_related('member','subscription','subscription__plan').get(pk=pk)
        elif code:
            payment = Payment.objects.select_related('member','subscription','subscription__plan').get(payment_code=code)
        elif receipt:
            payment = Payment.objects.select_related('member','subscription','subscription__plan').get(receipt_no=receipt)
        else:
            payment = Payment.objects.select_related('member','subscription','subscription__plan').order_by('-paid_at').first()
    except Payment.DoesNotExist:
        payment = None

    # Handle status change POST - only Paid/Pending allowed (no Failed)
    if request.method == "POST" and payment and 'status' in request.POST:
        new_status = request.POST.get('status', '').strip()
        if new_status in ('success', 'pending'):
            payment.status = new_status
            # Update paid_at accordingly
            if new_status == 'success' and not payment.paid_at:
                payment.paid_at = timezone.now()
            elif new_status != 'success':
                # Keep original paid_at or clear if failed? keep as is
                pass
            payment.save(update_fields=['status', 'paid_at'])
            messages.success(request, f"Payment {payment.payment_code} status updated to {new_status}.")
            return redirect(f"{request.path}?id={payment.id}")

    # Fetch linked receipt for "View Receipt" button (receipt.payment_id == payment.id)
    receipt = None
    if payment:
        try:
            # Try by payment_id FK, then by receipt_no
            receipt = Receipt.objects.filter(payment_id=payment.id).first()
            if not receipt and payment.receipt_no:
                receipt = Receipt.objects.filter(receipt_no=payment.receipt_no).first()
        except Exception:
            receipt = None

    return render(request, "payments/detail.html", {"current_user": get_current_user(request), "payment": payment, "receipt": receipt})

@login_required_custom
def refund_detail_view(request):
    return render(request, "refunds/detail.html", {"current_user": get_current_user(request)})

@login_required_custom
def refund_history_view(request):
    return render(request, "refunds/history.html", {"current_user": get_current_user(request)})

@login_required_custom
def statement_view(request):
    return render(request, "statement/list.html", {"current_user": get_current_user(request)})

@login_required_custom
def attendance_checkin_view(request):
    return render(request, "attendance/checkin.html")

def member_search_api_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse([], safe=False)

    members = Member.objects.filter(
        Q(full_name__icontains=query) | Q(member_code__icontains=query) | Q(phone__icontains=query)
    )[:10]

    results = [
        {
            "id": m.pk,
            "member_code": m.member_code,
            "full_name": m.full_name,
            "phone": m.phone,
            "email": m.email,
            "initials": m.initials,
            "status": m.status,
        }
        for m in members
    ]
    return JsonResponse(results, safe=False)

def attendance_checkin_save_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    member_id = request.POST.get('member_id')
    if not member_id:
        return JsonResponse({"success": False, "error": "member_id is required."}, status=400)

    try:
        member = Member.objects.get(pk=member_id)
    except (Member.DoesNotExist, ValueError):
        return JsonResponse({"success": False, "error": "Member not found."}, status=404)

    today = timezone.localdate()

    block_reason, subscription_status = _checkin_block_reason(member, today)
    if block_reason:
        return JsonResponse({
            "success": False,
            "blocked": True,
            "reason": block_reason,
            "subscription_status": subscription_status,
            "member_id": member.pk,
            "member_name": member.full_name,
        })

    attendance = Attendance.objects.filter(member=member, date=today).first()
    already_checked_in = attendance is not None

    if attendance is None:
        # duration_min is a Postgres GENERATED column — it can never appear in an
        # INSERT column list, so the ORM's normal create()/get_or_create() fails
        # against it. Insert only the real columns directly, then re-fetch via the ORM.
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO attendance (member_id, date, check_in) VALUES (%s, %s, %s) RETURNING id",
                    [member.pk, today, timezone.localtime().time()],
                )
                new_id = cursor.fetchone()[0]
            attendance = Attendance.objects.get(pk=new_id)
        except IntegrityError:
            # Lost a race with a concurrent check-in for the same member+day.
            already_checked_in = True
            attendance = Attendance.objects.get(member=member, date=today)

    check_in_display = attendance.check_in.strftime('%I:%M %p') if attendance.check_in else None

    if already_checked_in:
        message = f"{member.full_name} was already checked in today at {check_in_display}."
    else:
        message = f"{member.full_name} checked in at {check_in_display}."

    return JsonResponse({
        "success": True,
        "already_checked_in": already_checked_in,
        "member_id": member.pk,
        "member_name": member.full_name,
        "check_in": check_in_display,
        "message": message,
    })

def attendance_checkout_save_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    member_id = request.POST.get('member_id')
    if not member_id:
        return JsonResponse({"success": False, "error": "member_id is required."}, status=400)

    try:
        member = Member.objects.get(pk=member_id)
    except (Member.DoesNotExist, ValueError):
        return JsonResponse({"success": False, "error": "Member not found."}, status=404)

    today = timezone.localdate()
    attendance = Attendance.objects.filter(member=member, date=today).first()
    if attendance is None:
        return JsonResponse({
            "success": False,
            "error": f"{member.full_name} hasn't checked in today — check in before checking out.",
        }, status=404)

    already_checked_out = attendance.check_out is not None

    if not already_checked_out:
        # Same reasoning as check-in: duration_min is a Postgres GENERATED column
        # (check_out - check_in), so update only the real columns directly. Postgres
        # recomputes GENERATED columns as part of the row write itself, so this
        # UPDATE (issued via a raw cursor, same as the check-in raw INSERT) makes
        # duration_min correct immediately — no separate recompute step needed.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE attendance SET check_out = %s WHERE id = %s",
                [timezone.localtime().time(), attendance.pk],
            )
        attendance = Attendance.objects.get(pk=attendance.pk)

    check_out_display = attendance.check_out.strftime('%I:%M %p') if attendance.check_out else None

    if already_checked_out:
        message = f"{member.full_name} was already checked out today at {check_out_display}."
    else:
        message = f"{member.full_name} checked out at {check_out_display}."

    return JsonResponse({
        "success": True,
        "already_checked_out": already_checked_out,
        "member_id": member.pk,
        "member_name": member.full_name,
        "check_out": check_out_display,
        "duration_min": attendance.duration_min,
        "message": message,
    })

@login_required_custom
def expense_add_view(request):
    return render(request, "expenses/add.html", {"current_user": get_current_user(request)})

@login_required_custom
def subscription_renew_view(request, code):
    sub = get_object_or_404(Subscription.objects.select_related('member','plan'), subscription_code=code)
    plans = MembershipPlan.objects.filter(is_active=True).order_by('price')
    current_plan = sub.plan
    today = date.today()

    if request.method == "GET":
        # Show customizable renew page: upgrade/downgrade + recurring + promotion
        try:
            promotions = list(Promotion.objects.filter(is_active=True).order_by('code')[:20])
        except Exception:
            promotions = []
        return render(request, "subscriptions/renew.html", {
            "current_user": get_current_user(request),
            "subscription": sub,
            "plans": plans,
            "current_plan": current_plan,
            "today": today,
            "promotions": promotions,
        })

    # POST: handle customizable renew
    new_plan_id = request.POST.get('new_plan_id') or request.POST.get('plan_id')
    auto_renew = request.POST.get('auto_renew') == 'on' or request.POST.get('auto_renew') == 'true'
    # Determine target plan
    target_plan = current_plan
    is_upgrade = False
    is_downgrade = False
    price_diff = Decimal('0.00')
    if new_plan_id:
        try:
            target_plan = MembershipPlan.objects.get(pk=int(new_plan_id), is_active=True)
            if current_plan and target_plan.price > current_plan.price:
                is_upgrade = True
                price_diff = target_plan.price - current_plan.price
            elif current_plan and target_plan.price < current_plan.price:
                is_downgrade = True
                price_diff = current_plan.price - target_plan.price
        except (ValueError, MembershipPlan.DoesNotExist):
            target_plan = current_plan

    # Handle promotion
    promotion_code = request.POST.get('promotion_code', '').strip()
    promotion = None
    promotion_discount = Decimal('0.00')
    if promotion_code:
        try:
            promo = Promotion.objects.get(code__iexact=promotion_code, is_active=True)
            if promo.is_valid():
                promotion = promo
                # Apply to upgrade price diff, or to plan price if same plan
                base_amount = price_diff if is_upgrade else (target_plan.price if target_plan else Decimal('0.00'))
                if base_amount > 0:
                    _, discount = promo.apply(base_amount)
                    promotion_discount = discount
                    if is_upgrade:
                        price_diff = max(price_diff - discount, Decimal('0.00'))
            else:
                messages.warning(request, f"Promotion '{promotion_code}' is expired or inactive.")
        except Promotion.DoesNotExist:
            messages.warning(request, f"Promotion code '{promotion_code}' not found.")
        except Exception:
            pass

    # Handle recurring: if auto_renew checked, keep true, else set false
    # Also allow custom duration override for recurring
    duration = target_plan.duration_days if target_plan else 30
    # If recurring with custom interval, allow override (e.g., 30, 90, 180)
    recurring_interval = request.POST.get('recurring_interval')
    if recurring_interval:
        try:
            duration = int(recurring_interval)
            if duration <= 0:
                duration = target_plan.duration_days if target_plan else 30
        except ValueError:
            pass

    new_end = today + timedelta(days=duration)

    # Update subscription
    sub.plan = target_plan
    sub.start_date = today
    sub.end_date = new_end
    sub.status = "active"
    sub.auto_renew = auto_renew
    sub.save(update_fields=["plan", "start_date", "end_date", "status", "auto_renew"])

    cache.delete("sub_metrics")
    uid = request.session.get("user_id")
    cache.delete(f"page:subscriptions_view:{uid}:/subscriptions/")
    cache.delete(f"page:dashboard_view:{uid}:/dashboard/")

    # Handle payment for upgrade price difference if any
    if is_upgrade and price_diff > 0:
        try:
            from django.db import connection as conn
            with conn.cursor() as cur:
                cur.execute("SELECT setval(pg_get_serial_sequence('payments','id'), COALESCE((SELECT MAX(id) FROM payments),0)+1, false)")
        except Exception:
            pass
        try:
            # Create pending payment for upgrade difference (with promotion discount if any)
            Payment.objects.create(
                payment_code=f"PAY-UPG-{sub.id:04d}-{int(timezone.now().timestamp())%10000:04d}",
                receipt_no=f"RCPT-UPG-{sub.id:04d}",
                member=sub.member,
                subscription=sub,
                amount=price_diff + promotion_discount,
                discount=promotion_discount,
                total=price_diff,
                method='card',
                status='pending',
                paid_at=None,
            )
        except Exception:
            pass

    msg = f"Subscription {code} renewed: {today} → {new_end} ({target_plan.name if target_plan else ''})"
    if is_upgrade:
        msg += f" [Upgrade +${price_diff:.2f} pending]"
        if promotion:
            msg += f" (Promotion {promotion.code} -${promotion_discount:.2f})"
    elif is_downgrade:
        msg += f" [Downgrade -${price_diff:.2f} credit]"
    msg += f" {'(Recurring)' if auto_renew else ''}"
    if promotion and not is_upgrade:
        msg += f" [Promotion {promotion.code}]"
    messages.success(request, msg)

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        from django.http import JsonResponse
        return JsonResponse({"success": True, "code": code, "start": str(today), "end": str(new_end), "plan": target_plan.name if target_plan else "", "upgrade": is_upgrade, "downgrade": is_downgrade, "recurring": auto_renew})

    next_url = request.POST.get("next") or request.GET.get("next") or "/subscriptions/"
    return redirect(next_url)

@login_required_custom
def user_add_view(request, pk=None):
    # Adapted from users-backend to use dev's User model
    profile = None
    instance_user = None
    if pk is not None:
        try:
            profile = UserProfile.objects.select_related('user').get(pk=pk)
            instance_user = profile.user
        except Exception:
            # Fallback: treat pk as User pk
            try:
                instance_user = User.objects.get(pk=pk)
                profile = getattr(instance_user, 'profile', None)
            except Exception:
                instance_user = None

    if request.method == "POST":
        form = UserAddForm(request.POST, instance_user=instance_user)
        if form.is_valid():
            data = form.cleaned_data
            # Split full_name for dev's full_name field (dev stores full_name, not first/last)
            full_name = data['full_name'].strip()
            status_val = 'Active' if data['status'] == 'active' else 'Inactive'
            role_map = {'admin': 'Admin', 'accountant': 'Accountant', 'staff': 'Staff'}
            role_val = role_map.get(data['role'], data['role'].title() if isinstance(data['role'], str) else 'Admin')

            if instance_user is not None:
                instance_user.username = data['username']
                instance_user.email = data['email']
                instance_user.full_name = full_name
                instance_user.status = status_val
                instance_user.role = role_val if role_val in ['Admin','Accountant','Staff'] else 'Admin'
                if data['password']:
                    instance_user.set_password(data['password'])
                instance_user.save()
                if profile:
                    profile.role = data['role']
                    try:
                        profile.save(update_fields=['role'])
                    except Exception:
                        pass
                elif data['role']:
                    try:
                        UserProfile.objects.create(user=instance_user, role=data['role'])
                    except Exception:
                        pass
                messages.success(request, f'User "{instance_user.username}" was updated successfully.')
            else:
                # Fix sequence if out of sync (common after manual inserts with managed=False)
                try:
                    from django.db import connection
                    with connection.cursor() as cur:
                        cur.execute("SELECT setval(pg_get_serial_sequence('users','id'), COALESCE((SELECT MAX(id) FROM users),0)+1, false)")
                except Exception:
                    pass
                new_user = User(
                    username=data['username'],
                    email=data['email'],
                    full_name=full_name,
                    role=role_val if role_val in ['Admin','Accountant','Staff'] else 'Admin',
                    status=status_val,
                )
                new_user.set_password(data['password'])
                try:
                    new_user.save()
                except Exception as e:
                    # Retry once if duplicate key due to sequence lag
                    if 'duplicate key' in str(e).lower() or 'users_pkey' in str(e):
                        try:
                            from django.db import connection as conn2
                            with conn2.cursor() as cur2:
                                cur2.execute("SELECT COALESCE(MAX(id),0)+1 FROM users")
                                next_id = cur2.fetchone()[0]
                                new_user.id = next_id
                                new_user.save(force_insert=True)
                        except Exception as e2:
                            messages.error(request, f"Failed to create user: {e2}")
                            return render(request, "users/add.html", {"form": form, "is_edit": False, "edit_profile": profile, "current_user": get_current_user(request)})
                    else:
                        raise
                try:
                    UserProfile.objects.create(user=new_user, role=data['role'])
                except Exception:
                    pass
                messages.success(request, f'User "{data["username"]}" was created successfully.')
            return redirect('users')
    else:
        initial = None
        if instance_user is not None:
            initial = {
                'full_name': instance_user.full_name,
                'email': instance_user.email,
                'username': instance_user.username,
                'role': getattr(profile, 'role', instance_user.role.lower()) if profile and getattr(profile, 'role', None) else instance_user.role.lower(),
                'status': 'active' if instance_user.status == 'Active' else 'inactive',
            }
        form = UserAddForm(initial=initial, instance_user=instance_user)

    return render(request, "users/add.html", {
        "form": form,
        "is_edit": instance_user is not None,
        "edit_profile": profile,
        "current_user": get_current_user(request),
    })

@login_required_custom
def user_toggle_status_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    # Try UserProfile first, fallback to User
    try:
        profile = UserProfile.objects.select_related('user').get(pk=pk)
        user = profile.user
    except Exception:
        user = get_object_or_404(User, pk=pk)
        profile = None
    # Toggle status
    user.status = 'Inactive' if user.status == 'Active' else 'Active'
    user.save(update_fields=['status'])
    if profile and hasattr(user, 'is_active'):
        pass
    messages.success(request, "User enabled." if user.status == 'Active' else "User disabled.")
    return redirect('users')

@login_required_custom
def user_change_role_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        profile = UserProfile.objects.select_related('user').get(pk=pk)
        user = profile.user
    except Exception:
        user = get_object_or_404(User, pk=pk)
        profile = None
    new_role = request.POST.get('role')
    valid_roles = dict(UserProfile.ROLE_CHOICES) if 'UserProfile' in globals() else {'admin':'Admin','accountant':'Accountant','staff':'Staff'}
    # also allow dev roles
    role_lower_map = {'admin':'Admin','accountant':'Accountant','staff':'Staff'}
    if new_role not in valid_roles and new_role.lower() not in role_lower_map:
        messages.error(request, "Invalid role selected.")
        return redirect('users')
    # Update both User and Profile
    mapped_role = role_lower_map.get(new_role.lower(), new_role)
    if mapped_role in ['Admin','Accountant']:
        user.role = mapped_role
        user.save(update_fields=['role'])
    if profile:
        profile.role = new_role.lower()
        try:
            profile.save(update_fields=['role'])
        except Exception:
            pass
    else:
        try:
            UserProfile.objects.create(user=user, role=new_role.lower())
        except Exception:
            pass
    messages.success(request, f"Role updated.")
    return redirect('users')

@login_required_custom
def reports_generate_view(request):
    return render(request, "reports/generate.html")

def _build_rows_sql(report_type, date_range, date_from, date_to, status, category, plan):
    cfg = REPORT_TYPES[report_type]
    where_parts = []
    params = []

    if "{where}" not in cfg["base"]:
        pass
    elif report_type == "Expiring" or report_type == "Expired":
        pass
    else:
        pass

    sql = cfg["base"]
    dp = _date_condition(date_range, date_from, date_to, cfg["date_col"])
    if dp[0]:
        where_parts.append(dp[0])
        params.extend(dp[1])

    if status and "status_map" in cfg:
        mapped = cfg["status_map"].get(status)
        if mapped:
            where_parts.append(mapped)

    if category and cfg.get("category_filter"):
        where_parts.append("e.category = %s")
        params.append(category)

    if plan and cfg.get("plan_filter"):
        where_parts.append("p.name = %s")
        params.append(plan)

    if where_parts:
        if "WHERE 1=1" in sql:
            sql += " AND " + " AND ".join(where_parts)
        elif "WHERE" in sql:
            sql += " AND " + " AND ".join(where_parts)

    sql += " ORDER BY 1 DESC NULLS LAST"
    return sql, params

def _build_chart_sql(report_type, date_range, date_from, date_to, status, category, plan):
    cfg = REPORT_TYPES[report_type]
    sql = cfg["chart_sql"]
    params = []

    where_parts = []
    dp = _date_condition(date_range, date_from, date_to, cfg["date_col"])
    if dp[0]:
        where_parts.append(dp[0])
        params.extend(dp[1])

    if status and "status_map" in cfg:
        mapped = cfg["status_map"].get(status)
        if mapped:
            where_parts.append(mapped)

    if category and cfg.get("category_filter"):
        where_parts.append("e.category = %s")
        params.append(category)

    if plan and cfg.get("plan_filter"):
        where_parts.append("p.name = %s")
        params.append(plan)

    if where_parts:
        joined = " AND ".join(where_parts)
        if "{where}" in sql:
            prefix = sql[:sql.index("{where}")]
            if re.search(r"\bWHERE\b", prefix, re.IGNORECASE):
                sql = sql.replace("{where}", " AND " + joined)
            else:
                sql = sql.replace("{where}", " WHERE " + joined)
        else:
            sql += " AND " + joined
    else:
        sql = sql.replace("{where}", "")

    return sql, params

def _render_row(report_type, row_dict):
    """Convert a raw row dict into the generic 5-column format."""
    d = row_dict
    if report_type == "Revenue":
        return {
            "date": d["paid_at"].strftime("%Y-%m-%d") if d.get("paid_at") else "",
            "metric_id": d.get("payment_code") or "",
            "category": "SUBSCRIPTIONS",
            "value": float(d.get("total") or 0),
            "value_display": f"${float(d.get('total') or 0):,.2f}",
            "status": "SUCCESS",
        }
    if report_type == "Payments":
        status = (d.get("status") or "").upper()
        return {
            "date": d["paid_at"].strftime("%Y-%m-%d") if d.get("paid_at") else "",
            "metric_id": d.get("payment_code") or d.get("receipt_no") or "",
            "category": (d.get("method") or "").upper(),
            "value": float(d.get("amount") or 0),
            "value_display": f"${float(d.get('amount') or 0):,.2f}",
            "status": status,
        }
    if report_type == "Subscriptions":
        return {
            "date": d["start_date"].strftime("%Y-%m-%d") if d.get("start_date") else "",
            "metric_id": d.get("subscription_code") or "",
            "category": (d.get("plan_name") or "N/A").upper(),
            "value": float(d.get("plan_price") or 0),
            "value_display": f"${float(d.get('plan_price') or 0):,.2f}",
            "status": (d.get("status") or "").upper(),
        }
    if report_type == "Expenses":
        return {
            "date": d["expense_date"].strftime("%Y-%m-%d") if d.get("expense_date") else "",
            "metric_id": d.get("expense_code") or "",
            "category": (d.get("category") or "").upper(),
            "value": float(d.get("amount") or 0),
            "value_display": f"${float(d.get('amount') or 0):,.2f}",
            "status": (d.get("status") or "").upper(),
        }
    if report_type == "Refunds":
        status = (d.get("status") or "").upper()
        return {
            "date": d["created_at"].strftime("%Y-%m-%d") if d.get("created_at") else "",
            "metric_id": d.get("refund_code") or "",
            "category": (d.get("reason") or "REFUND").upper()[:30],
            "value": float(d.get("amount") or 0),
            "value_display": f"${float(d.get('amount') or 0):,.2f}",
            "status": status,
        }
    if report_type == "Members":
        return {
            "date": d["join_date"].strftime("%Y-%m-%d") if d.get("join_date") else "",
            "metric_id": d.get("member_code") or "",
            "category": (d.get("status") or "").upper(),
            "value": 1,
            "value_display": "1",
            "status": (d.get("status") or "").upper(),
        }
    if report_type == "Attendance":
        ci = d.get("check_in")
        co = d.get("check_out")
        time_str = ""
        if ci:
            time_str = ci.strftime("%H:%M")
            if co:
                time_str += "-" + co.strftime("%H:%M")
        return {
            "date": d["date"].strftime("%Y-%m-%d") if d.get("date") else "",
            "metric_id": d.get("member_name") or f"#{d.get('member_id')}",
            "category": "CHECK-IN" if not co else "CHECK-OUT",
            "value": d.get("duration_min") or 0,
            "value_display": f"{d.get('duration_min') or 0} min",
            "status": "COMPLETE" if co else "OPEN",
        }
    if report_type in ("Expiring", "Expired"):
        end = d.get("end_date")
        days = ""
        if end:
            from datetime import date as _date
            diff = (end - _date.today()).days
            days = f" ({diff}d)" if diff >= 0 else f" ({abs(diff)}d ago)"
        return {
            "date": end.strftime("%Y-%m-%d") if end else "",
            "metric_id": d.get("subscription_code") or "",
            "category": (d.get("plan_name") or "N/A").upper(),
            "value": 1,
            "value_display": "1",
            "status": ("EXPIRING" if report_type == "Expiring" else "EXPIRED") + days,
        }
    return {
        "date": "",
        "metric_id": "",
        "category": "",
        "value": 0,
        "value_display": "$0.00",
        "status": "",
    }


@require_http_methods(["GET"])

def reports_api(request):
    report_type = request.GET.get("report_type", "Revenue")
    if report_type not in REPORT_TYPES:
        return JsonResponse({"ok": False, "error": f"Invalid report type: {report_type}"}, status=400)

    date_range = request.GET.get("date_range", "last_30")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    status = request.GET.get("status", "")
    category = request.GET.get("category", "")
    plan = request.GET.get("plan", "")

    try:
        rows_sql, rows_params = _build_rows_sql(
            report_type, date_range, date_from, date_to, status, category, plan
        )
        with connection.cursor() as cur:
            cur.execute(rows_sql, rows_params)
            cols = [d[0] for d in cur.description]
            raw_rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        chart_sql, chart_params = _build_chart_sql(
            report_type, date_range, date_from, date_to, status, category, plan
        )
        with connection.cursor() as cur:
            cur.execute(chart_sql, chart_params)
            chart_cols = [d[0] for d in cur.description]
            chart_raw = [dict(zip(chart_cols, row)) for row in cur.fetchall()]

        from calendar import month_abbr as _ma
        today = localdate()
        chart_labels = []
        chart_series = []
        chart_map = {}
        for cr in chart_raw:
            ym = cr.get("ym", "")
            if ym:
                chart_map[ym] = float(cr.get(REPORT_TYPES[report_type]["chart_value"], 0))

        year, month = today.year, today.month
        for i in range(5, -1, -1):
            y, m = year, month - i
            while m <= 0:
                m += 12
                y -= 1
            ym = "%04d-%02d" % (y, m)
            chart_labels.append(_ma[m].upper())
            chart_series.append(chart_map.get(ym, 0))

        rows = [_render_row(report_type, r) for r in raw_rows]

        if report_type in ("Revenue", "Payments", "Expenses", "Refunds", "Subscriptions"):
            total_value = sum(r["value"] for r in rows)
        else:
            total_value = len(rows)

        if report_type in ("Revenue", "Payments", "Expenses", "Refunds"):
            value_display = f"${total_value:,.2f}"
        else:
            value_display = str(int(total_value))

        return JsonResponse({
            "ok": True,
            "kpi": {
                "total_value": value_display,
                "total_value_num": total_value,
                "record_count": len(rows),
                "report_type": report_type,
            },
            "chart": {
                "labels": chart_labels,
                "series": chart_series,
            },
            "rows": rows,
            "total_rows": len(rows),
        })

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@login_required_custom


def _expense_form_context(expense=None):
    context = {
        'categories': Expense.CATEGORY_CHOICES,
        'methods': Expense.PAYMENT_METHOD_CHOICES,
        'statuses': Expense.STATUS_CHOICES,
        'today': timezone.localdate(),
    }
    if expense is not None:
        context['expense'] = expense
    return context

def _next_expense_no():
    prefix = "EXP-"
    count = Expense.objects.filter(expense_code__startswith=prefix).count()
    number = count + 1
    while True:
        candidate = f"{prefix}{number:04d}"
        if not Expense.objects.filter(expense_code=candidate).exists():
            return candidate
        number += 1

def _next_refund_no():
    prefix = "RFD-"
    count = Refund.objects.filter(refund_code__startswith=prefix).count()
    number = count + 1
    while True:
        candidate = f"{prefix}{number:04d}"
        if not Refund.objects.filter(refund_code=candidate).exists():
            return candidate
        number += 1

def _refund_status_badges():
    return Refund.STATUS_CHOICES


@never_cache

def expense_delete_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    expense = get_object_or_404(Expense, pk=pk)
    expense.delete()
    return redirect('expenses')

def expense_edit_view(request, pk):
    expense = get_object_or_404(Expense, pk=pk)

    if request.method == "POST":
        category = request.POST.get('category', '').strip()
        description = request.POST.get('description', '').strip()
        amount = _decimal(request.POST.get('amount'))
        payment_method = request.POST.get('payment_method', '').strip()
        expense_date = _parse_date(request.POST.get('expense_date')) or expense.expense_date or timezone.localdate()
        notes = request.POST.get('notes', '').strip()
        status = request.POST.get('status', 'pending').strip()

        if not category:
            return render(request, "expenses/add.html", {
                **_expense_form_context(expense),
                'error': "Category is required.",
            })
        if amount <= 0:
            return render(request, "expenses/add.html", {
                **_expense_form_context(expense),
                'error': "Amount must be greater than zero.",
            })
        if not description:
            return render(request, "expenses/add.html", {
                **_expense_form_context(expense),
                'error': "Description is required.",
            })

        expense.category = category
        expense.description = description
        expense.amount = amount
        expense.payment_method = payment_method
        expense.expense_date = expense_date
        expense.notes = notes or None
        expense.status = status if status in dict(Expense.STATUS_CHOICES) else 'pending'
        expense.save()

        return redirect('expenses')

    return render(request, "expenses/add.html", _expense_form_context(expense))

@never_cache
def notification_mark_all_read_view(request):
    for n in get_notifications():
        if not n.read:
            NotificationRead.objects.get_or_create(nkey=n.key)
    return redirect('notifications')

@never_cache
def notification_mark_read_view(request, nkey):
    NotificationRead.objects.get_or_create(nkey=nkey)
    return redirect('notifications')


@never_cache

@never_cache
def refund_create_view(request):
    payments_qs = Payment.objects.select_related('member').order_by('-paid_at', '-id')
    payment_options = [(p, p.refundable_amount()) for p in payments_qs]

    if request.method == "POST":
        payment_id = request.POST.get('payment_id') or None
        amount = _decimal(request.POST.get('amount'))
        reason = request.POST.get('reason', '').strip()
        confirmed = request.POST.get('confirm') == '1'
        ctx = {
            'payments': payment_options,
            'statuses': Refund.STATUS_CHOICES,
        }

        payment = None
        if payment_id:
            try:
                payment = Payment.objects.get(pk=payment_id)
            except (Payment.DoesNotExist, ValueError):
                return render(request, "refunds/create.html", {
                    **ctx,
                    'error': "Selected payment no longer exists.",
                })
        else:
            return render(request, "refunds/create.html", {
                **ctx,
                'error': "Please select a payment to refund.",
            })

        if amount <= 0:
            return render(request, "refunds/create.html", {
                **ctx,
                'selected_payment': payment,
                'error': "Refund amount must be greater than zero.",
            })
        if amount > payment.refundable_amount():
            return render(request, "refunds/create.html", {
                **ctx,
                'selected_payment': payment,
                'error': f"Refund cannot exceed refundable amount (${payment.refundable_amount():.2f}).",
            })
        if len(reason) < 5:
            return render(request, "refunds/create.html", {
                **ctx,
                'selected_payment': payment,
                'error': "Please provide a reason (at least 5 characters).",
            })
        if not confirmed:
            return render(request, "refunds/create.html", {
                **ctx,
                'selected_payment': payment,
                'error': "You must confirm the refund authorization.",
            })

        refund = Refund(
            refund_code=_next_refund_no(),
            payment=payment,
            amount=amount,
            reason=reason,
            status='pending',
        )
        try:
            refund.save()
        except IntegrityError:
            refund.refund_code = _next_refund_no()
            refund.save()

        return redirect(f"{reverse('refund-detail', kwargs={'pk': refund.pk})}?created=1")

    return render(request, "refunds/create.html", {
        'payments': payment_options,
        'statuses': Refund.STATUS_CHOICES,
    })

@never_cache

def refund_status_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    refund = get_object_or_404(Refund, pk=pk)
    action = request.POST.get('action')
    if refund.status != 'pending':
        return render(request, "refunds/detail.html", {
            'refund': refund,
            'statuses': Refund.STATUS_CHOICES,
            'error': "Only pending refunds can be approved or rejected.",
        })
    if action == 'approve':
        refund.status = 'approved'
    elif action == 'reject':
        refund.status = 'rejected'
    else:
        return JsonResponse({'error': 'Unknown action.'}, status=400)
    refund.save()
    return redirect(f"{reverse('refund-detail', kwargs={'pk': refund.pk})}?updated=1")


@never_cache

def receipt_custom_view(request):
    return render(request, "receipts/custom.html", {"current_user": get_current_user(request)})
