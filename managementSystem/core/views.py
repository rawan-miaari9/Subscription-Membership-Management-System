from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, connection
from django.db.models import Q
from django.shortcuts import redirect, render

from .forms import MemberForm
from .models import Member

def login_view(request):
    return render(request, "auth/login.html")

def dashboard_view(request):
    return render(request, "dashboard/index.html")

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

def member_add_view(request):
    if request.method == "POST":
        form = MemberForm(request.POST)
        if form.is_valid():
            member = _create_member(form.cleaned_data)
            if member is None:
                messages.error(request, "Could not save this member due to a conflict — please try again.")
                return render(request, "members/add.html", {"form": form})

            messages.success(request, f'Member "{member.full_name}" was added successfully ({member.member_code}).')
            return redirect('members')
    else:
        form = MemberForm()

    return render(request, "members/add.html", {"form": form})

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
