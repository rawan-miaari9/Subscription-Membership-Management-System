from django.contrib import messages
from django.contrib.auth.models import User
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from .forms import UserAddForm
from .models import UserProfile

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
    user_profiles = UserProfile.objects.select_related('user').order_by(
        'user__first_name', 'user__last_name'
    )
    context = {
        'user_profiles': user_profiles,
        'total_active': user_profiles.filter(user__is_active=True).count(),
        'admin_count': user_profiles.filter(role=UserProfile.ROLE_ADMIN).count(),
        'accountant_count': user_profiles.filter(role=UserProfile.ROLE_ACCOUNTANT).count(),
        'staff_count': user_profiles.filter(role=UserProfile.ROLE_STAFF).count(),
    }
    return render(request, "users/list.html", context)

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

def expense_add_view(request):
    return render(request, "expenses/add.html")

def user_add_view(request, pk=None):
    profile = None
    instance_user = None
    if pk is not None:
        profile = get_object_or_404(UserProfile.objects.select_related('user'), pk=pk)
        instance_user = profile.user

    if request.method == "POST":
        form = UserAddForm(request.POST, instance_user=instance_user)
        if form.is_valid():
            data = form.cleaned_data
            name_parts = data['full_name'].strip().split(None, 1)
            first_name = name_parts[0] if name_parts else ''
            last_name = name_parts[1] if len(name_parts) > 1 else ''

            if instance_user is not None:
                instance_user.username = data['username']
                instance_user.email = data['email']
                instance_user.first_name = first_name
                instance_user.last_name = last_name
                instance_user.is_active = data['status'] == 'active'
                if data['password']:
                    instance_user.set_password(data['password'])
                instance_user.save()

                profile.role = data['role']
                profile.save(update_fields=['role'])

                messages.success(request, f"User \"{instance_user.username}\" was updated successfully.")
            else:
                new_user = User(
                    username=data['username'],
                    email=data['email'],
                    first_name=first_name,
                    last_name=last_name,
                    is_active=data['status'] == 'active',
                )
                new_user.set_password(data['password'])
                new_user.save()
                UserProfile.objects.create(user=new_user, role=data['role'])

                messages.success(request, f"User \"{data['username']}\" was created successfully.")
            return redirect('users')
    else:
        initial = None
        if instance_user is not None:
            initial = {
                'full_name': instance_user.get_full_name() or instance_user.username,
                'email': instance_user.email,
                'username': instance_user.username,
                'role': profile.role,
                'status': 'active' if instance_user.is_active else 'inactive',
            }
        form = UserAddForm(initial=initial, instance_user=instance_user)

    return render(request, "users/add.html", {
        "form": form,
        "is_edit": instance_user is not None,
        "edit_profile": profile,
    })

def user_toggle_status_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    profile = get_object_or_404(UserProfile.objects.select_related('user'), pk=pk)
    user = profile.user
    user.is_active = not user.is_active
    user.save(update_fields=['is_active'])

    messages.success(request, "User enabled." if user.is_active else "User disabled.")
    return redirect('users')

def reports_generate_view(request):
    return render(request, "reports/generate.html")

def receipt_custom_view(request):
    return render(request, "receipts/custom.html")
