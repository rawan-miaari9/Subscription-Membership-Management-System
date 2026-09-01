from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, connection
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

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
    })

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
