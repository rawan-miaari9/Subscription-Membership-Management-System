import json
import re
from functools import wraps
from decimal import Decimal, InvalidOperation
from datetime import datetime, date, timedelta
from django.core.cache import cache
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db import transaction
from django.views.decorators.http import require_http_methods
from django.contrib.auth.hashers import make_password
from .models import User, Member, MembershipPlan, Subscription, BusinessInformation, FinancialSetting, PaymentMethod, NotificationSetting


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


@login_required_custom
@per_user_page_cache(300)
def dashboard_view(request):
    user = get_current_user(request)
    return render(request, "dashboard/index.html", {"current_user": user})

@login_required_custom
def members_view(request):
    return render(request, "members/list.html", {"current_user": get_current_user(request)})

@login_required_custom
def plans_view(request):
    return render(request, "plans/list.html", {"current_user": get_current_user(request)})

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
def payments_view(request):
    return render(request, "payments/list.html", {"current_user": get_current_user(request)})

@login_required_custom
def invoices_view(request):
    return render(request, "invoices/detail.html", {"current_user": get_current_user(request)})

@login_required_custom
def renewals_view(request):
    return render(request, "renewals/list.html", {"current_user": get_current_user(request)})

@login_required_custom
def refunds_view(request):
    return render(request, "refunds/list.html", {"current_user": get_current_user(request)})

@login_required_custom
def attendance_view(request):
    return render(request, "attendance/index.html", {"current_user": get_current_user(request)})

@login_required_custom
def expenses_view(request):
    return render(request, "expenses/list.html", {"current_user": get_current_user(request)})

@login_required_custom
def notifications_view(request):
    return render(request, "notifications/index.html", {"current_user": get_current_user(request)})

@login_required_custom
def reports_view(request):
    return render(request, "reports/index.html", {"current_user": get_current_user(request)})

@login_required_custom
def users_view(request):
    return render(request, "users/list.html", {"current_user": get_current_user(request)})

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
        # Current model avatar is CharField - store filename; if ImageField, it will handle file
        try:
            if admin.avatar and hasattr(admin.avatar, 'delete'):
                admin.avatar.delete(save=False)
            admin.avatar = request.FILES["avatar"]
        except Exception:
            # Fallback for CharField: store name
            admin.avatar = request.FILES["avatar"].name
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
def member_detail_view(request):
    return render(request, "members/detail.html", {"current_user": get_current_user(request)})

@login_required_custom
def member_add_view(request):
    return render(request, "members/add.html", {"current_user": get_current_user(request)})

@login_required_custom
def services_view(request):
    return render(request, "services/list.html", {"current_user": get_current_user(request)})

@login_required_custom
def plan_create_view(request):
    return render(request, "plans/create.html", {"current_user": get_current_user(request)})

@login_required_custom
def plan_custom_view(request):
    return render(request, "plans/custom.html", {"current_user": get_current_user(request)})

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
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            except ValueError:
                errors["start_date"] = "Invalid start date."
        if not end_date_str:
            errors["end_date"] = "End date required."
        else:
            try:
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
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
def payment_detail_view(request):
    return render(request, "payments/detail.html", {"current_user": get_current_user(request)})

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
    return render(request, "attendance/checkin.html", {"current_user": get_current_user(request)})

@login_required_custom
def expense_add_view(request):
    return render(request, "expenses/add.html", {"current_user": get_current_user(request)})

@login_required_custom
def subscription_renew_view(request, code):
    # Renew same subscription: reactivate with same plan/duration from today
    sub = get_object_or_404(Subscription, subscription_code=code)
    if request.method != "POST":
        return redirect(f"/subscriptions/detail/?code={code}")
    # compute new dates from today using plan duration
    today = date.today()
    duration = sub.plan.duration_days if sub.plan else 30
    new_end = today + timedelta(days=duration)
    # update same row: keep same code, same member/plan, new dates, active
    sub.start_date = today
    sub.end_date = new_end
    sub.status = "active"
    sub.save(update_fields=["start_date", "end_date", "status"])
    cache.delete("sub_metrics")
    uid = request.session.get("user_id")
    cache.delete(f"page:subscriptions_view:{uid}:/subscriptions/")
    cache.delete(f"page:dashboard_view:{uid}:/dashboard/")
    messages.success(request, f"Subscription {code} renewed: {today} → {new_end} ({sub.plan.name if sub.plan else ''})")
    # support AJAX
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        from django.http import JsonResponse
        return JsonResponse({"success": True, "code": code, "start": str(today), "end": str(new_end)})
    next_url = request.POST.get("next") or request.GET.get("next") or "/subscriptions/"
    return redirect(next_url)

@login_required_custom
def user_add_view(request):
    return render(request, "users/add.html", {"current_user": get_current_user(request)})

@login_required_custom
def reports_generate_view(request):
    return render(request, "reports/generate.html", {"current_user": get_current_user(request)})

@login_required_custom
def receipt_custom_view(request):
    return render(request, "receipts/custom.html", {"current_user": get_current_user(request)})
