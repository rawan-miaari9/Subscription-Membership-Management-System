import datetime

from django.core.paginator import Paginator
from django.db import IntegrityError, connection
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None

from .models import Attendance, Member, Subscription


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

def login_view(request):
    return render(request, "auth/login.html")

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

def expense_add_view(request):
    return render(request, "expenses/add.html")

def user_add_view(request):
    return render(request, "users/add.html")

def reports_generate_view(request):
    return render(request, "reports/generate.html")

def receipt_custom_view(request):
    return render(request, "receipts/custom.html")
