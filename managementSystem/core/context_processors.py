from .notifications import unread_count


def notifications_processor(request):
    """Expose the unread notification count for the header bell badge."""
    from django.conf import settings
    try:
        count = unread_count()
    except Exception:
        count = 0
    return {'unread_notifications': count}
