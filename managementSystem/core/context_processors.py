from .notifications import unread_count


def notifications_processor(request):
    """Expose unread count + recent 5 for header dropdown (LinkedIn style)."""
    from django.conf import settings
    try:
        count = unread_count()
    except Exception:
        count = 0
    try:
        from .notifications import get_notifications
        recent = get_notifications()[:5]
    except Exception:
        recent = []
    return {'unread_notifications': count, 'header_notifications': recent}
