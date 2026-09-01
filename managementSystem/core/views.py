from django.db import IntegrityError, connection
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from .models import Attendance, Member

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
