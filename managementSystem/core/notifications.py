"""Notification generation for the Notification Center.

Notifications are derived live from the underlying business tables (subscriptions,
payments, refunds) so they stay current with the data. Each notification carries a
stable `key` used to persist read/unread state in NotificationRead.

Only enabled notification categories (from core_notificationsetting) are generated.
"""

from datetime import timedelta

from django.utils import timezone

from .models import (Member, NotificationRead, NotificationSetting, Payment,
                     Refund, Subscription)

# Days within which a subscription is considered "expiring soon".
EXPIRING_SOON_DAYS = 30
# Days before expiry to send a renewal reminder.
RENEWAL_REMINDER_DAYS = 7


def _days_text(delta):
    d = delta.days
    if d <= 0:
        return "today"
    if d == 1:
        return "in 1 day"
    return f"in {d} days"


def _relative(dt_or_date):
    """Human relative time for a datetime or date."""
    if dt_or_date is None:
        return ""
    now = timezone.now()
    if hasattr(dt_or_date, 'date'):
        dt = dt_or_date
    else:
        dt = timezone.make_aware(timezone.datetime.combine(dt_or_date, timezone.datetime.min.time()))
    delta = now - dt
    secs = delta.total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} hr ago"
    if secs < 86400 * 7:
        return f"{int(secs // 86400)} days ago"
    return dt.strftime("%b %d, %Y")


class Notification:
    def __init__(self, ntype, title, message, icon, tone, key, meta=None,
                 created=None, url=None, btn_text=None):
        self.ntype = ntype          # expiration | renewal | expired | payment | refund
        self.title = title
        self.message = message
        self.icon = icon            # material symbol name
        self.tone = tone            # warn | success | info | danger
        self.key = key              # stable key for read tracking
        self.meta = meta or {}
        self.created = created or timezone.now()
        self.url = url
        self.btn_text = btn_text
        self.read = False

    @property
    def relative(self):
        return _relative(self.created)


_TYPE_LABELS = {
    'expiration': ('Expiring Soon', 'EXPIRATION'),
    'renewal': ('Renewal Reminder', 'RENEWAL'),
    'expired': ('Expired', 'EXPIRED'),
    'payment': ('Payment Received', 'PAYMENT'),
    'refund': ('Refund', 'REFUND'),
}


def _pending_notifications():
    """Generate the full list of notifications (read state applied later)."""
    notifications = []
    today = timezone.localdate()
    soon = today + timedelta(days=EXPIRING_SOON_DAYS)
    renewal_cutoff = today + timedelta(days=RENEWAL_REMINDER_DAYS)

    # ---- Subscriptions: expiration / renewal / expired ----
    subs = Subscription.objects.select_related('member').order_by('end_date')
    for sub in subs:
        member = sub.member
        member_name = member.full_name if member else "A member"
        member_code = member.member_code if member else ""
        end = sub.end_date

        if not end:
            continue

        # Expired subscription notification
        if sub.status == 'expired' or end < today:
            if NotificationSetting.is_enabled('expiration'):
                notifications.append(Notification(
                    'expired', 'Expired',
                    f"{member_name}'s subscription ({sub.subscription_code}) expired on {end.strftime('%b %d, %Y')}.",
                    'block', 'danger',
                    key=f"expired-{sub.id}",
                    created=timezone.now() - timedelta(days=max((today - end).days, 0)),
                    url=f"/members/{member.id}/" if member else None,
                    btn_text="View member" if member else None,
                    meta={'code': member_code, 'end': end},
                ))
            continue

        days_left = (end - today).days

        # Expiring soon notification
        if end <= soon and days_left >= 0:
            if NotificationSetting.is_enabled('expiration'):
                notifications.append(Notification(
                    'expiration', 'Expiring Soon',
                    f"{member_name}'s membership ends {_days_text(end - today)} on {end.strftime('%b %d, %Y')}. "
                    f"Review to keep the member active.",
                    'timer', 'warn',
                    key=f"expiration-{sub.id}",
                    created=timezone.now() - timedelta(days=max(EXPIRING_SOON_DAYS - days_left, 0)),
                    url=f"/members/{member.id}/" if member else None,
                    btn_text="View member" if member else None,
                    meta={'code': member_code, 'end': end, 'days_left': days_left},
                ))

        # Renewal reminder notification
        if days_left <= RENEWAL_REMINDER_DAYS and days_left >= 0:
            if NotificationSetting.is_enabled('renewal'):
                if sub.auto_renew:
                    msg = (f"{member_name}'s subscription renews {_days_text(end - today)} ({end.strftime('%b %d, %Y')}). "
                           f"Automatic renewal {'is enabled' if sub.auto_renew else 'is disabled'}; confirm next payment.")
                else:
                    msg = (f"{member_name}'s subscription expires {_days_text(end - today)} ({end.strftime('%b %d, %Y')}) "
                           f"and will NOT auto-renew. Send a renewal reminder.")
                notifications.append(Notification(
                    'renewal', 'Renewal Reminder',
                    msg,
                    'autorenew', 'success',
                    key=f"renewal-{sub.id}",
                    created=timezone.now() - timedelta(days=max(RENEWAL_REMINDER_DAYS - days_left, 0)),
                    url=f"/members/{member.id}/" if member else None,
                    btn_text="Renew" if member else None,
                    meta={'code': member_code, 'end': end, 'days_left': days_left},
                ))

    # ---- Payments ----
    for pmt in Payment.objects.select_related('member').order_by('-paid_at')[:20]:
        member = pmt.member
        member_name = member.full_name if member else "Walk-in / Guest"
        method = (pmt.method or '').upper() or 'PAYMENT'
        notifications.append(Notification(
            'payment', 'Payment Received',
            f"{pmt.payment_code or f'Payment #{pmt.id}'} of ${pmt.total or 0:.2f} received from {member_name} ({method}).",
            'check_circle', 'success',
            key=f"payment-{pmt.id}",
            created=pmt.paid_at or pmt.id and timezone.now(),
            url="/payments/",
            btn_text="View payment",
            meta={'code': pmt.payment_code, 'amount': pmt.total},
        ))

    # ---- Refunds (approved) ----
    for refund in Refund.objects.select_related('member', 'payment').filter(status='approved').order_by('-created_at')[:20]:
        member = refund.member
        member_name = member.full_name if member else "a member"
        refunds_refunded_total = refund.payment.refunded_amount() if refund.payment else refund.amount
        notifications.append(Notification(
            'refund', 'Refund',
            f"Refund {refund.refund_code or f'#{refund.id}'} of ${refund.amount or 0:.2f} was approved and issued to {member_name} "
            f"against {getattr(refund.payment, 'payment_code', 'payment') or 'payment'}.",
            'currency_exchange', 'info',
            key=f"refund-{refund.id}",
            created=refund.created_at or timezone.now(),
            url=f"/refunds/{refund.id}/",
            btn_text="View refund",
            meta={'code': refund.refund_code, 'amount': refund.amount,
                  'member_code': member.member_code if member else ''},
        ))

    return notifications


def get_notifications(apply_read=True):
    """Return all notifications sorted newest-first, with read state applied from NotificationRead."""
    notifications = _pending_notifications()

    if apply_read and notifications:
        keys = {n.key for n in notifications}
        read_keys = set(
            NotificationRead.objects.filter(nkey__in=keys).values_list('nkey', flat=True)
        )
        for n in notifications:
            n.read = n.key in read_keys

    notifications.sort(key=lambda n: n.created, reverse=True)
    return notifications


def unread_count():
    return sum(1 for n in get_notifications() if not n.read)
