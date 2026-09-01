from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, connection
from django.shortcuts import redirect, render

from .forms import PlanForm
from .models import MembershipPlan

def login_view(request):
    return render(request, "auth/login.html")

def dashboard_view(request):
    return render(request, "dashboard/index.html")

def members_view(request):
    return render(request, "members/list.html")

def plans_view(request):
    plans_qs = MembershipPlan.objects.all().order_by('name')
    paginator = Paginator(plans_qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    context = {
        'plans': page_obj.object_list,
        'page_obj': page_obj,
    }
    return render(request, "plans/list.html", context)

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
    return render(request, "settings/index.html")

def member_detail_view(request):
    return render(request, "members/detail.html")

def member_add_view(request):
    return render(request, "members/add.html")

def services_view(request):
    return render(request, "services/list.html")

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

def plan_create_view(request):
    if request.method == "POST":
        duration_bucket = request.POST.get('duration', '')

        if duration_bucket == 'custom':
            # Custom-duration plans are a separate flow (templates/plans/custom.html,
            # its own sibling task) with a different set of fields entirely
            # (services checkboxes, etc.) — this form can't represent one, so
            # send the admin to the flow that actually can rather than showing
            # a dead-end validation error.
            messages.info(
                request,
                'This form is for standard, fixed-duration plans. Use "Create Custom Plan" for custom/negotiated tiers.',
            )
            return redirect('plan-custom')

        post_data = request.POST.copy()
        post_data['duration_days'] = str(PLAN_DURATION_DAYS_BY_BUCKET.get(duration_bucket, ''))
        # This view only ever creates standard (non-custom) plans — force
        # is_fixed True regardless of the checkbox's absence from this
        # template, rather than let an unchecked/missing checkbox silently
        # default to False.
        post_data['is_fixed'] = 'on'

        form = PlanForm(post_data)
        if form.is_valid():
            data = form.cleaned_data
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
                return render(request, "plans/create.html", {"form": form, "submitted_duration": duration_bucket})

            plan = MembershipPlan.objects.get(pk=new_id)
            messages.success(request, f'Membership plan "{plan.name}" was created successfully.')
            return redirect('plans')

        return render(request, "plans/create.html", {"form": form, "submitted_duration": duration_bucket})

    form = PlanForm()
    return render(request, "plans/create.html", {"form": form, "submitted_duration": ""})

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
