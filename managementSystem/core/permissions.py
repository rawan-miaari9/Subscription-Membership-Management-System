from functools import wraps

from django.shortcuts import redirect, render


def role_required(*allowed_roles):
    """Restrict a view to logged-in users whose role is in allowed_roles.

    Supports both:
    - Django auth User + UserProfile (users-backend original)
    - Dev's custom session User (get_current_user) with role string
    - Admin-controlled Accountant permissions via AccountantPermission
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            # First try dev's custom session auth (priority, as dev is main auth)
            try:
                from .views import get_current_user
                custom_user = get_current_user(request)
            except Exception:
                custom_user = None
            if custom_user:
                role = (custom_user.role or "").lower()
                allowed_lower = [r.lower() for r in allowed_roles]
                try:
                    profile = getattr(custom_user, 'profile', None)
                    if profile and getattr(profile, 'role', None):
                        role = profile.role.lower()
                except Exception:
                    pass
                if role not in allowed_lower:
                    return render(request, "errors/403.html", status=403)
                # For Accountant, check Admin-controlled per-page permission
                if role == 'accountant':
                    try:
                        from .models import AccountantPermission
                        # Map view name to permission code
                        view_code_map = {
                            'dashboard_view': 'dashboard',
                            'members_view': 'members',
                            'plans_view': 'plans',
                            'subscriptions_view': 'subscriptions',
                            'pricing_view': 'pricing',
                            'payments_view': 'payments',
                            'invoices_view': 'invoices',
                            'invoice_create_view': 'invoices',
                            'invoice_edit_view': 'invoices',
                            'invoice_detail_view': 'invoices',
                            'renewals_view': 'renewals',
                            'refunds_view': 'refunds',
                            'attendance_view': 'attendance',
                            'expenses_view': 'expenses',
                            'notifications_view': 'notifications',
                            'reports_view': 'reports',
                            'reports_generate_view': 'reports',
                            'users_view': 'users',
                            'settings_view': 'settings',
                            'statement_view': 'statement',
                        }
                        code = view_code_map.get(view_func.__name__)
                        if code and not AccountantPermission.is_allowed(code):
                            return render(request, "errors/403.html", status=403)
                    except Exception:
                        pass
                return view_func(request, *args, **kwargs)

            if not getattr(request.user, 'is_authenticated', False):
                return redirect('login')
            profile = getattr(request.user, 'profile', None)
            role = getattr(profile, 'role', None)
            if role not in allowed_roles:
                if role and role.lower() not in [r.lower() for r in allowed_roles]:
                    return render(request, "errors/403.html", status=403)
                elif not role:
                    return render(request, "errors/403.html", status=403)
            return view_func(request, *args, **kwargs)
        return wrapped_view
    return decorator
