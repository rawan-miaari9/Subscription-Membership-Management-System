from functools import wraps

from django.shortcuts import redirect, render


def role_required(*allowed_roles):
    """Restrict a view to logged-in users whose UserProfile.role is in allowed_roles.

    Anonymous users are redirected to the login page. Authenticated users whose
    role isn't allowed (or who have no UserProfile/role yet) get a 403 page.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')

            profile = getattr(request.user, 'profile', None)
            role = getattr(profile, 'role', None)

            if role not in allowed_roles:
                return render(request, "errors/403.html", status=403)

            return view_func(request, *args, **kwargs)
        return wrapped_view
    return decorator
