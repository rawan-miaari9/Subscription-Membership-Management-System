from functools import wraps
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from .models import User


def get_current_user(request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        return None


def login_required_custom(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = get_current_user(request)
        if not user:
            # preserve next url
            next_url = request.get_full_path()
            login_url = f"/?next={next_url}" if next_url != "/" else "/login/"
            # use /login/ as canonical
            if request.path != "/login/" and request.path != "/":
                return redirect(f"/login/?next={next_url}")
            return redirect("/login/")
        # inject user into request for templates
        request.current_user = user
        return view_func(request, *args, **kwargs)
    return wrapper


def login_view(request):
    # if already logged in, redirect to dashboard
    if get_current_user(request):
        return redirect("dashboard")

    error = None
    email_prefill = ""

    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        role = request.POST.get("role", "").strip()  # Admin / Accountant
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
                    # success - set session
                    request.session["user_id"] = user.id
                    request.session["user_email"] = user.email
                    request.session["user_role"] = user.role
                    request.session["user_name"] = user.full_name
                    request.session["username"] = user.username
                    request.session["avatar"] = user.avatar or ""

                    if remember:
                        # 30 days
                        request.session.set_expiry(60 * 60 * 24 * 30)
                    else:
                        request.session.set_expiry(0)

                    # update updated_at as last login
                    User.objects.filter(id=user.id).update(updated_at=timezone.now())

                    if next_url and next_url.startswith("/"):
                        return redirect(next_url)
                    return redirect("dashboard")

    # inject next for template
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
def subscriptions_view(request):
    return render(request, "subscriptions/list.html", {"current_user": get_current_user(request)})

def pricing_view(request):
    # public or protected? keep protected
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

@login_required_custom
def settings_view(request):
    return render(request, "settings/index.html", {"current_user": get_current_user(request)})

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
    return render(request, "subscriptions/detail.html", {"current_user": get_current_user(request)})

@login_required_custom
def subscription_create_view(request):
    return render(request, "subscriptions/create.html", {"current_user": get_current_user(request)})

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
def user_add_view(request):
    return render(request, "users/add.html", {"current_user": get_current_user(request)})

@login_required_custom
def reports_generate_view(request):
    return render(request, "reports/generate.html", {"current_user": get_current_user(request)})

@login_required_custom
def receipt_custom_view(request):
    return render(request, "receipts/custom.html", {"current_user": get_current_user(request)})
