import datetime
from decimal import Decimal, InvalidOperation

from django.core.paginator import Paginator
from django.db import IntegrityError, connection
from django.db.models import F, Q, Sum, Case, When, DecimalField
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.cache import never_cache


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _decimal(value, default=Decimal('0.00')):
    try:
        return Decimal(str(value).strip())
    except (TypeError, ValueError, InvalidOperation):
        return default


from .models import (Attendance, Expense, Financial, Invoice, Member, Payment, Receipt, Refund, Subscription)


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
    today = timezone.localdate()
    month_start = today.replace(day=1)
    soon_end = today + datetime.timedelta(days=30)

    stats = {
        'active_members': Member.objects.filter(status='active').count(),
        'sent_invoices': Invoice.objects.filter(status='sent').count(),
        'outstanding': Invoice.objects.filter(status__in=['sent', 'partial']).aggregate(
            t=Sum(F('total') - F('amount_paid'))
        )['t'] or 0,
        'monthly_revenue': Receipt.objects.filter(paid_date__gte=month_start).aggregate(
            t=Sum('total')
        )['t'] or 0,
        'monthly_invoiced': Invoice.objects.exclude(status='void').filter(
            issued_date__gte=month_start
        ).aggregate(t=Sum('total'))['t'] or 0,
        'expiring_soon': Subscription.objects.filter(
            status__in=['active', 'expiring'],
            end_date__gte=today,
            end_date__lte=soon_end,
        ).count(),
        'today_attendance': Attendance.objects.filter(date=today).count(),
    }
    return render(request, "dashboard/index.html", stats)

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
    invoices_qs = Invoice.objects.select_related('member').order_by('-issued_date', '-id')

    status = request.GET.get('status', '').strip()
    search = request.GET.get('q', '').strip()
    today = timezone.localdate()

    if search:
        invoices_qs = invoices_qs.filter(
            Q(invoice_no__icontains=search) | Q(member__full_name__icontains=search)
        )
    if status == 'overdue':
        invoices_qs = invoices_qs.exclude(status__in=['paid', 'void']).filter(
            due_date__lt=today, amount_paid__lt=F('total')
        )
    elif status == 'partial':
        invoices_qs = invoices_qs.filter(amount_paid__gt=0, amount_paid__lt=F('total'))
    elif status == 'paid':
        invoices_qs = invoices_qs.filter(status='paid')
    elif status in ('draft', 'sent', 'void'):
        invoices_qs = invoices_qs.filter(status=status)

    paginator = Paginator(invoices_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Preserve active filters across Previous/Next links.
    filters_querydict = request.GET.copy()
    filters_querydict.pop('page', None)
    filters_querystring = filters_querydict.urlencode()

    # Summary stats (only for the current filter set).
    stats_qs = invoices_qs
    totals = stats_qs.aggregate(
        total_billed=Sum('total'),
        total_paid=Sum('amount_paid'),
    )
    outstanding = (totals['total_billed'] or 0) - (totals['total_paid'] or 0)
    overdue_count = stats_qs.exclude(status__in=['paid', 'void']).filter(
        due_date__lt=today, amount_paid__lt=F('total')
    ).count()

    context = {
        'invoices': page_obj.object_list,
        'page_obj': page_obj,
        'filters_querystring': filters_querystring,
        'filter_status': status,
        'filter_search': search,
        'filters_active': bool(status or search),
        'stat_total': stats_qs.count(),
        'stat_billed': totals['total_billed'] or 0,
        'stat_outstanding': outstanding,
        'stat_overdue': overdue_count,
        'today': today,
    }
    return render(request, "invoices/list.html", context)


def _next_invoice_no():
    year = timezone.now().year
    prefix = f"INV-{year}-"
    count = Invoice.objects.filter(invoice_no__startswith=prefix).count()
    number = count + 1
    while True:
        candidate = f"{prefix}{number:04d}"
        if not Invoice.objects.filter(invoice_no=candidate).exists():
            return candidate
        number += 1


def invoice_create_view(request):
    if request.method == "POST":
        member_id = request.POST.get('member_id') or None
        bill_to = request.POST.get('bill_to', '').strip()
        description = request.POST.get('description', '').strip()
        amount = _decimal(request.POST.get('amount'))
        discount_type = request.POST.get('discount_type', 'flat').strip()
        discount_value = _decimal(request.POST.get('discount_value'))
        tax_rate = Financial.get_tax_rate()
        payment_terms = request.POST.get('payment_terms', 'due_on_receipt').strip()
        issued_date = _parse_date(request.POST.get('issued_date')) or timezone.localdate()
        due_date = _parse_date(request.POST.get('due_date'))
        status = request.POST.get('status', 'draft').strip()
        amount_paid = _decimal(request.POST.get('amount_paid'))
        invoice_no = request.POST.get('invoice_no', '').strip()
        notes = request.POST.get('notes', '').strip()

        member = None
        if member_id:
            try:
                member = Member.objects.get(pk=member_id)
            except (Member.DoesNotExist, ValueError):
                return render(request, "invoices/create.html", {
                    'error': "Selected member no longer exists.",
                })

        if invoice_no and Invoice.objects.filter(invoice_no=invoice_no).exclude(pk=None).exists():
            invoice_no = _next_invoice_no()
        if not invoice_no:
            invoice_no = _next_invoice_no()

        invoice = Invoice(
            invoice_no=invoice_no,
            bill_to=bill_to or None,
            member=member,
            description=description or None,
            subtotal=amount,
            discount_type=discount_type if discount_type in ('flat', 'percent') else 'flat',
            discount=discount_value,
            tax_rate=tax_rate,
            payment_terms=payment_terms,
            issued_date=issued_date,
            due_date=due_date,
            status=status if status in dict(Invoice.STATUS_CHOICES) else 'draft',
            amount_paid=amount_paid,
            notes=notes or None,
        )
        invoice.recalculate()
        if invoice.status == 'paid':
            invoice.amount_paid = invoice.total

        try:
            invoice.save()
        except IntegrityError:
            invoice.invoice_no = _next_invoice_no()
            invoice.save()

        return redirect('invoice-detail', pk=invoice.pk)

    context = {
        'terms': Invoice.TERM_CHOICES,
        'status_choices': Invoice.STATUS_CHOICES,
        'next_invoice_no': _next_invoice_no(),
        'today': timezone.localdate(),
        'settings_tax_rate': Financial.get_tax_rate(),
    }
    return render(request, "invoices/create.html", context)


def invoice_edit_view(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)

    if request.method == "POST":
        member_id = request.POST.get('member_id') or None
        bill_to = request.POST.get('bill_to', '').strip()
        description = request.POST.get('description', '').strip()
        amount = _decimal(request.POST.get('amount'))
        discount_type = request.POST.get('discount_type', 'flat').strip()
        discount_value = _decimal(request.POST.get('discount_value'))
        tax_rate = Financial.get_tax_rate()
        payment_terms = request.POST.get('payment_terms', 'due_on_receipt').strip()
        issued_date = _parse_date(request.POST.get('issued_date')) or timezone.localdate()
        due_date = _parse_date(request.POST.get('due_date'))
        status = request.POST.get('status', 'draft').strip()
        amount_paid = _decimal(request.POST.get('amount_paid'))
        invoice_no = request.POST.get('invoice_no', '').strip() or invoice.invoice_no
        notes = request.POST.get('notes', '').strip()

        member = None
        if member_id:
            try:
                member = Member.objects.get(pk=member_id)
            except (Member.DoesNotExist, ValueError):
                return render(request, "invoices/create.html", {
                    'invoice': invoice,
                    'error': "Selected member no longer exists.",
                })

        if invoice_no and Invoice.objects.filter(invoice_no=invoice_no).exclude(pk=invoice.pk).exists():
            invoice_no = _next_invoice_no()

        invoice.invoice_no = invoice_no
        invoice.bill_to = bill_to or None
        invoice.member = member
        invoice.description = description or None
        invoice.subtotal = amount
        invoice.discount_type = discount_type if discount_type in ('flat', 'percent') else 'flat'
        invoice.discount = discount_value
        invoice.tax_rate = tax_rate
        invoice.payment_terms = payment_terms
        invoice.issued_date = issued_date
        invoice.due_date = due_date
        invoice.status = status if status in dict(Invoice.STATUS_CHOICES) else 'draft'
        invoice.amount_paid = amount_paid
        invoice.notes = notes or None
        invoice.recalculate()
        if invoice.status == 'paid':
            invoice.amount_paid = invoice.total

        try:
            invoice.save()
        except IntegrityError:
            invoice.invoice_no = _next_invoice_no()
            invoice.save()

        return redirect('invoice-detail', pk=invoice.pk)

    context = {
        'invoice': invoice,
        'terms': Invoice.TERM_CHOICES,
        'status_choices': Invoice.STATUS_CHOICES,
        'next_invoice_no': _next_invoice_no(),
        'today': timezone.localdate(),
        'settings_tax_rate': Financial.get_tax_rate(),
    }
    return render(request, "invoices/create.html", context)


def invoice_detail_view(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('member'), pk=pk)
    return render(request, "invoices/detail.html", {'invoice': invoice, 'today': timezone.localdate()})


def invoice_pdf_view(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('member'), pk=pk)
    try:
        from weasyprint import HTML
        html = render_to_string("invoices/pdf.html", {'invoice': invoice})
        pdf_bytes = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        if request.GET.get('debug'):
            return HttpResponse(
                f"PDF error: {type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
                .replace('\n', '<br>'),
                status=500,
            )
        return HttpResponse(
            "PDF rendering failed. The invoice cannot be exported right now; "
            "try View in browser or contact support.",
            status=503,
        )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    disposition = "attachment" if request.GET.get('download') == '1' else "inline"
    response['Content-Disposition'] = f'{disposition}; filename="{invoice.invoice_no}.pdf"'
    return response


def invoice_status_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    invoice = get_object_or_404(Invoice, pk=pk)
    action = request.POST.get('action')
    if action == 'mark_paid':
        invoice.amount_paid = invoice.total
        invoice.status = 'paid'
    elif action == 'mark_sent':
        invoice.status = 'sent'
    elif action == 'void':
        invoice.status = 'void'
    else:
        return JsonResponse({'error': 'Unknown action.'}, status=400)
    invoice.save()
    return redirect('invoice-detail', pk=invoice.pk)

def renewals_view(request):
    return render(request, "renewals/list.html")

def _next_refund_no():
    prefix = "RFD-"
    count = Refund.objects.filter(refund_code__startswith=prefix).count()
    number = count + 1
    while True:
        candidate = f"{prefix}{number:04d}"
        if not Refund.objects.filter(refund_code=candidate).exists():
            return candidate
        number += 1


def _refund_status_badges():
    return Refund.STATUS_CHOICES


@never_cache
def refunds_view(request):
    refunds_qs = Refund.objects.select_related('payment', 'member').order_by('-created_at', '-id')

    search = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()

    if search:
        refunds_qs = refunds_qs.filter(
            Q(refund_code__icontains=search)
            | Q(payment__payment_code__icontains=search)
            | Q(member__full_name__icontains=search)
            | Q(reason__icontains=search)
        )
    if status:
        refunds_qs = refunds_qs.filter(status=status)

    pending_count = refunds_qs.filter(status='pending').count()
    approved_total = refunds_qs.filter(status='approved').aggregate(t=Sum('amount'))['t'] or 0
    rejected_total = refunds_qs.filter(status='rejected').aggregate(t=Sum('amount'))['t'] or 0

    paginator = Paginator(refunds_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    filters_querydict = request.GET.copy()
    filters_querydict.pop('page', None)
    filters_querystring = filters_querydict.urlencode()

    context = {
        'refunds': page_obj.object_list,
        'page_obj': page_obj,
        'filters_querystring': filters_querystring,
        'filter_search': search,
        'filter_status': status,
        'filters_active': bool(search or status),
        'stat_count': refunds_qs.count(),
        'stat_pending': pending_count,
        'stat_approved': approved_total,
        'stat_rejected': rejected_total,
        'statuses': Refund.STATUS_CHOICES,
        'today': timezone.localdate(),
    }
    return render(request, "refunds/list.html", context)

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
    expenses_qs = Expense.objects.all()

    search = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    status = request.GET.get('status', '').strip()
    date_from = _parse_date(request.GET.get('date_from', '').strip())
    date_to = _parse_date(request.GET.get('date_to', '').strip())

    if search:
        expenses_qs = expenses_qs.filter(
            Q(expense_code__icontains=search)
            | Q(description__icontains=search)
            | Q(notes__icontains=search)
        )
    if category:
        expenses_qs = expenses_qs.filter(category=category)
    if status:
        expenses_qs = expenses_qs.filter(status=status)
    if date_from:
        expenses_qs = expenses_qs.filter(expense_date__gte=date_from)
    if date_to:
        expenses_qs = expenses_qs.filter(expense_date__lte=date_to)

    totals = expenses_qs.aggregate(total_amount=Sum('amount'))
    stat_total = totals['total_amount'] or 0
    stat_count = expenses_qs.count()

    pending_total = (
        expenses_qs.filter(status='pending').aggregate(t=Sum('amount'))['t'] or 0
    )
    cleared_total = (
        expenses_qs.filter(status='cleared').aggregate(t=Sum('amount'))['t'] or 0
    )

    paginator = Paginator(expenses_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    filters_querydict = request.GET.copy()
    filters_querydict.pop('page', None)
    filters_querystring = filters_querydict.urlencode()

    context = {
        'expenses': page_obj.object_list,
        'page_obj': page_obj,
        'filters_querystring': filters_querystring,
        'filter_search': search,
        'filter_category': category,
        'filter_status': status,
        'filter_date_from': date_from,
        'filter_date_to': date_to,
        'filters_active': bool(search or category or status or date_from or date_to),
        'stat_total': stat_total,
        'stat_count': stat_count,
        'stat_pending': pending_total,
        'stat_cleared': cleared_total,
        'categories': Expense.CATEGORY_CHOICES,
        'statuses': Expense.STATUS_CHOICES,
        'today': timezone.localdate(),
    }
    return render(request, "expenses/list.html", context)

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

@never_cache
def refund_detail_view(request, pk):
    refund = get_object_or_404(Refund.objects.select_related('payment__member', 'member'), pk=pk)
    success = None
    if request.GET.get('created'):
        success = f"Refund {refund.refund_code or refund.pk} was submitted and is now pending approval."
    elif request.GET.get('updated'):
        new = refund.status
        if new == 'approved':
            success = f"Refund {refund.refund_code or refund.pk} was approved and will appear on the member's statement."
        elif new == 'rejected':
            success = f"Refund {refund.refund_code or refund.pk} was rejected."
    return render(request, "refunds/detail.html", {
        'refund': refund,
        'statuses': Refund.STATUS_CHOICES,
        'success': success,
    })


def refund_history_view(request):
    return refunds_view(request)


def refund_status_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    refund = get_object_or_404(Refund, pk=pk)
    action = request.POST.get('action')
    if refund.status != 'pending':
        return render(request, "refunds/detail.html", {
            'refund': refund,
            'statuses': Refund.STATUS_CHOICES,
            'error': "Only pending refunds can be approved or rejected.",
        })
    if action == 'approve':
        refund.status = 'approved'
    elif action == 'reject':
        refund.status = 'rejected'
    else:
        return JsonResponse({'error': 'Unknown action.'}, status=400)
    refund.save()
    return redirect(f"{reverse('refund-detail', kwargs={'pk': refund.pk})}?updated=1")


@never_cache
def refund_create_view(request):
    payments_qs = Payment.objects.select_related('member').order_by('-paid_at', '-id')
    payment_options = [(p, p.refundable_amount()) for p in payments_qs]

    if request.method == "POST":
        payment_id = request.POST.get('payment_id') or None
        amount = _decimal(request.POST.get('amount'))
        reason = request.POST.get('reason', '').strip()
        confirmed = request.POST.get('confirm') == '1'
        ctx = {
            'payments': payment_options,
            'statuses': Refund.STATUS_CHOICES,
        }

        payment = None
        if payment_id:
            try:
                payment = Payment.objects.get(pk=payment_id)
            except (Payment.DoesNotExist, ValueError):
                return render(request, "refunds/create.html", {
                    **ctx,
                    'error': "Selected payment no longer exists.",
                })
        else:
            return render(request, "refunds/create.html", {
                **ctx,
                'error': "Please select a payment to refund.",
            })

        if amount <= 0:
            return render(request, "refunds/create.html", {
                **ctx,
                'selected_payment': payment,
                'error': "Refund amount must be greater than zero.",
            })
        if amount > payment.refundable_amount():
            return render(request, "refunds/create.html", {
                **ctx,
                'selected_payment': payment,
                'error': f"Refund cannot exceed refundable amount (${payment.refundable_amount():.2f}).",
            })
        if len(reason) < 5:
            return render(request, "refunds/create.html", {
                **ctx,
                'selected_payment': payment,
                'error': "Please provide a reason (at least 5 characters).",
            })
        if not confirmed:
            return render(request, "refunds/create.html", {
                **ctx,
                'selected_payment': payment,
                'error': "You must confirm the refund authorization.",
            })

        refund = Refund(
            refund_code=_next_refund_no(),
            payment=payment,
            amount=amount,
            reason=reason,
            status='pending',
        )
        try:
            refund.save()
        except IntegrityError:
            refund.refund_code = _next_refund_no()
            refund.save()

        return redirect(f"{reverse('refund-detail', kwargs={'pk': refund.pk})}?created=1")

    return render(request, "refunds/create.html", {
        'payments': payment_options,
        'statuses': Refund.STATUS_CHOICES,
    })

@never_cache
def statement_view(request):
    members = Member.objects.order_by('full_name')
    member_id = request.GET.get('member')
    member = None
    if member_id:
        try:
            member = Member.objects.get(pk=member_id)
        except (Member.DoesNotExist, ValueError):
            member = None
    if member is None and members:
        member = members.first()

    rows = []
    balance = Decimal('0.00')
    totals = {
        'charge': Decimal('0.00'),
        'payment': Decimal('0.00'),
        'discount': Decimal('0.00'),
        'refund': Decimal('0.00'),
    }

    if member is not None:
        invoices_qs = Invoice.objects.filter(member=member).order_by('issued_date', 'id')
        payments_qs = Payment.objects.filter(member=member).order_by('paid_at', 'id')
        refunds_qs = Refund.objects.filter(member=member, status='approved').order_by('created_at', 'id')

        # Opening balance
        rows.append({
            'date': None,
            'description': 'Opening Balance',
            'charge': None, 'payment': None, 'discount': None, 'refund': None,
            'balance': Decimal('0.00'),
        })

        for inv in invoices_qs:
            disc = inv.discount_amount
            charge = inv.subtotal or Decimal('0.00')
            balance = balance + (inv.total or Decimal('0.00'))
            rows.append({
                'date': inv.issued_date,
                'description': f"Invoice {inv.invoice_no}" + (f" — {inv.description}" if inv.description else ""),
                'charge': charge,
                'payment': None,
                'discount': disc if disc > 0 else None,
                'refund': None,
                'balance': balance,
            })
            totals['charge'] += charge
            totals['discount'] += disc

        for pmt in payments_qs:
            balance = balance - (pmt.total or Decimal('0.00'))
            rows.append({
                'date': pmt.paid_at.date() if pmt.paid_at else None,
                'description': f"Payment {pmt.payment_code} — {pmt.get_method_display()}",
                'charge': None,
                'payment': pmt.total,
                'discount': None,
                'refund': None,
                'balance': balance,
            })
            totals['payment'] += pmt.total or Decimal('0.00')

        for refund in refunds_qs:
            balance = balance - (refund.amount or Decimal('0.00'))
            rows.append({
                'date': refund.created_at.date() if refund.created_at else None,
                'description': f"Refund {refund.refund_code}" + (f" — {refund.reason}" if refund.reason else ""),
                'charge': None,
                'payment': None,
                'discount': None,
                'refund': refund.amount,
                'balance': balance,
            })
            totals['refund'] += refund.amount or Decimal('0.00')

    return render(request, "statement/list.html", {
        'members': members,
        'selected_member': member,
        'rows': rows,
        'totals': totals,
        'balance': balance,
    })

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

def _next_expense_no():
    prefix = "EXP-"
    count = Expense.objects.filter(expense_code__startswith=prefix).count()
    number = count + 1
    while True:
        candidate = f"{prefix}{number:04d}"
        if not Expense.objects.filter(expense_code=candidate).exists():
            return candidate
        number += 1


def _expense_form_context(expense=None):
    context = {
        'categories': Expense.CATEGORY_CHOICES,
        'methods': Expense.PAYMENT_METHOD_CHOICES,
        'statuses': Expense.STATUS_CHOICES,
        'today': timezone.localdate(),
    }
    if expense is not None:
        context['expense'] = expense
    return context


def expense_add_view(request):
    if request.method == "POST":
        category = request.POST.get('category', '').strip()
        description = request.POST.get('description', '').strip()
        amount = _decimal(request.POST.get('amount'))
        payment_method = request.POST.get('payment_method', '').strip()
        expense_date = _parse_date(request.POST.get('expense_date')) or timezone.localdate()
        notes = request.POST.get('notes', '').strip()
        status = request.POST.get('status', 'pending').strip()

        if not category:
            return render(request, "expenses/add.html", {
                **_expense_form_context(),
                'error': "Category is required.",
            })
        if amount <= 0:
            return render(request, "expenses/add.html", {
                **_expense_form_context(),
                'error': "Amount must be greater than zero.",
            })
        if not description:
            return render(request, "expenses/add.html", {
                **_expense_form_context(),
                'error': "Description is required.",
            })

        expense = Expense(
            expense_code=_next_expense_no(),
            category=category,
            description=description,
            amount=amount,
            payment_method=payment_method,
            expense_date=expense_date,
            notes=notes or None,
            status=status if status in dict(Expense.STATUS_CHOICES) else 'pending',
        )
        try:
            expense.save()
        except IntegrityError:
            expense.expense_code = _next_expense_no()
            expense.save()

        return redirect('expenses')

    return render(request, "expenses/add.html", _expense_form_context())


def expense_edit_view(request, pk):
    expense = get_object_or_404(Expense, pk=pk)

    if request.method == "POST":
        category = request.POST.get('category', '').strip()
        description = request.POST.get('description', '').strip()
        amount = _decimal(request.POST.get('amount'))
        payment_method = request.POST.get('payment_method', '').strip()
        expense_date = _parse_date(request.POST.get('expense_date')) or expense.expense_date or timezone.localdate()
        notes = request.POST.get('notes', '').strip()
        status = request.POST.get('status', 'pending').strip()

        if not category:
            return render(request, "expenses/add.html", {
                **_expense_form_context(expense),
                'error': "Category is required.",
            })
        if amount <= 0:
            return render(request, "expenses/add.html", {
                **_expense_form_context(expense),
                'error': "Amount must be greater than zero.",
            })
        if not description:
            return render(request, "expenses/add.html", {
                **_expense_form_context(expense),
                'error': "Description is required.",
            })

        expense.category = category
        expense.description = description
        expense.amount = amount
        expense.payment_method = payment_method
        expense.expense_date = expense_date
        expense.notes = notes or None
        expense.status = status if status in dict(Expense.STATUS_CHOICES) else 'pending'
        expense.save()

        return redirect('expenses')

    return render(request, "expenses/add.html", _expense_form_context(expense))


def expense_delete_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    expense = get_object_or_404(Expense, pk=pk)
    expense.delete()
    return redirect('expenses')

def user_add_view(request):
    return render(request, "users/add.html")

def reports_generate_view(request):
    return render(request, "reports/generate.html")

def receipts_view(request):
    receipts_qs = Receipt.objects.select_related('member').order_by('-paid_date', '-id')

    search = request.GET.get('q', '').strip()
    method = request.GET.get('method', '').strip()

    if search:
        receipts_qs = receipts_qs.filter(
            Q(receipt_no__icontains=search) | Q(member__full_name__icontains=search) | Q(bill_to__icontains=search)
        )
    if method:
        receipts_qs = receipts_qs.filter(method=method)

    paginator = Paginator(receipts_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    filters_querydict = request.GET.copy()
    filters_querydict.pop('page', None)
    filters_querystring = filters_querydict.urlencode()

    stats = receipts_qs.aggregate(stat_total=Sum('total'), stat_tax=Sum('tax_amount'))
    discount_expr = Case(
        When(discount_type='percent', then=F('subtotal') * F('discount') / Decimal('100')),
        default=F('discount'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    stats['stat_discount'] = receipts_qs.aggregate(d=Sum(discount_expr))['d'] or 0

    context = {
        'receipts': page_obj.object_list,
        'page_obj': page_obj,
        'filters_querystring': filters_querystring,
        'filter_method': method,
        'filter_search': search,
        'filters_active': bool(method or search),
        'stat_count': receipts_qs.count(),
        'stat_total': stats['stat_total'] or 0,
        'stat_tax': stats['stat_tax'] or 0,
        'stat_discount': stats['stat_discount'] or 0,
        'methods': Receipt.METHOD_CHOICES,
        'today': timezone.localdate(),
    }
    return render(request, "receipts/list.html", context)


def _next_receipt_no():
    year = timezone.now().year
    prefix = f"RCT-{year}-"
    count = Receipt.objects.filter(receipt_no__startswith=prefix).count()
    number = count + 1
    while True:
        candidate = f"{prefix}{number:04d}"
        if not Receipt.objects.filter(receipt_no=candidate).exists():
            return candidate
        number += 1


def _process_logo(request, receipt=None):
    """Return a stored logo value (data URI) from an optional upload.
    New file → base64 data URI; remove_logo → None; otherwise keep existing."""
    upload = request.FILES.get('logo')
    if upload:
        data = upload.read()
        if len(data) > 512 * 1024:
            raise ValueError("Logo image must be smaller than 512 KB.")
        import base64
        mime = upload.content_type or 'image/png'
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    if request.POST.get('remove_logo') == '1':
        return None
    if receipt is not None:
        return receipt.logo
    return None


def receipt_create_view(request):
    if request.method == "POST":
        member_id = request.POST.get('member_id') or None
        bill_to = request.POST.get('bill_to', '').strip()
        description = request.POST.get('description', '').strip()
        amount = _decimal(request.POST.get('amount'))
        discount_type = request.POST.get('discount_type', 'flat').strip()
        discount_value = _decimal(request.POST.get('discount_value'))
        tax_rate = Financial.get_tax_rate()
        method = request.POST.get('method', 'cash').strip()
        paid_date = _parse_date(request.POST.get('paid_date')) or timezone.localdate()
        notes = request.POST.get('notes', '').strip()

        member = None
        if member_id:
            try:
                member = Member.objects.get(pk=member_id)
            except (Member.DoesNotExist, ValueError):
                return render(request, "receipts/create.html", {
                    'error': "Selected member no longer exists.",
                    'methods': Receipt.METHOD_CHOICES,
                    'today': timezone.localdate(),
                    'next_receipt_no': _next_receipt_no(),
                })

        try:
            logo = _process_logo(request)
        except ValueError as exc:
            return render(request, "receipts/create.html", {
                'error': str(exc),
                'methods': Receipt.METHOD_CHOICES,
                'today': timezone.localdate(),
                'next_receipt_no': _next_receipt_no(),
            })

        receipt_no = request.POST.get('receipt_no', '').strip() or _next_receipt_no()
        if Receipt.objects.filter(receipt_no=receipt_no).exists():
            receipt_no = _next_receipt_no()

        receipt = Receipt(
            receipt_no=receipt_no,
            bill_to=bill_to or None,
            member=member,
            description=description or None,
            subtotal=amount,
            discount_type=discount_type if discount_type in ('flat', 'percent') else 'flat',
            discount=discount_value,
            tax_rate=tax_rate,
            method=method if method in dict(Receipt.METHOD_CHOICES) else 'cash',
            paid_date=paid_date,
            notes=notes or None,
            logo=logo,
        )
        receipt.recalculate()

        try:
            receipt.save()
        except IntegrityError:
            receipt.receipt_no = _next_receipt_no()
            receipt.save()

        return redirect('receipt-detail', pk=receipt.pk)

    context = {
        'methods': Receipt.METHOD_CHOICES,
        'next_receipt_no': _next_receipt_no(),
        'today': timezone.localdate(),
        'settings_tax_rate': Financial.get_tax_rate(),
    }
    return render(request, "receipts/create.html", context)


def receipt_edit_view(request, pk):
    receipt = get_object_or_404(Receipt, pk=pk)

    if request.method == "POST":
        member_id = request.POST.get('member_id') or None
        bill_to = request.POST.get('bill_to', '').strip()
        description = request.POST.get('description', '').strip()
        amount = _decimal(request.POST.get('amount'))
        discount_type = request.POST.get('discount_type', 'flat').strip()
        discount_value = _decimal(request.POST.get('discount_value'))
        tax_rate = Financial.get_tax_rate()
        method = request.POST.get('method', 'cash').strip()
        paid_date = _parse_date(request.POST.get('paid_date')) or timezone.localdate()
        receipt_no = request.POST.get('receipt_no', '').strip() or receipt.receipt_no
        notes = request.POST.get('notes', '').strip()

        member = None
        if member_id:
            try:
                member = Member.objects.get(pk=member_id)
            except (Member.DoesNotExist, ValueError):
                return render(request, "receipts/create.html", {
                    'receipt': receipt,
                    'error': "Selected member no longer exists.",
                })

        try:
            logo = _process_logo(request, receipt)
        except ValueError as exc:
            return render(request, "receipts/create.html", {
                'receipt': receipt,
                'error': str(exc),
            })

        if receipt_no and Receipt.objects.filter(receipt_no=receipt_no).exclude(pk=receipt.pk).exists():
            receipt_no = _next_receipt_no()

        receipt.receipt_no = receipt_no
        receipt.bill_to = bill_to or None
        receipt.member = member
        receipt.description = description or None
        receipt.subtotal = amount
        receipt.discount_type = discount_type if discount_type in ('flat', 'percent') else 'flat'
        receipt.discount = discount_value
        receipt.tax_rate = tax_rate
        receipt.method = method if method in dict(Receipt.METHOD_CHOICES) else 'cash'
        receipt.paid_date = paid_date
        receipt.notes = notes or None
        receipt.logo = logo
        receipt.recalculate()

        try:
            receipt.save()
        except IntegrityError:
            receipt.receipt_no = _next_receipt_no()
            receipt.save()

        return redirect('receipt-detail', pk=receipt.pk)

    context = {
        'receipt': receipt,
        'methods': Receipt.METHOD_CHOICES,
        'next_receipt_no': _next_receipt_no(),
        'today': timezone.localdate(),
        'settings_tax_rate': Financial.get_tax_rate(),
    }
    return render(request, "receipts/create.html", context)


def receipt_detail_view(request, pk):
    receipt = get_object_or_404(Receipt.objects.select_related('member'), pk=pk)
    return render(request, "receipts/detail.html", {'receipt': receipt, 'today': timezone.localdate()})


def receipt_delete_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    receipt = get_object_or_404(Receipt, pk=pk)
    receipt_no = receipt.receipt_no
    receipt.delete()
    return redirect('receipts')


def receipt_pdf_view(request, pk):
    receipt = get_object_or_404(Receipt.objects.select_related('member'), pk=pk)
    try:
        from weasyprint import HTML
        html = render_to_string("receipts/pdf.html", {'receipt': receipt})
        pdf_bytes = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        if request.GET.get('debug'):
            return HttpResponse(
                f"PDF error: {type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
                .replace('\n', '<br>'),
                status=500,
            )
        return HttpResponse(
            "PDF rendering failed. The receipt cannot be exported right now; try again.",
            status=503,
        )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    disposition = "attachment" if request.GET.get('download') == '1' else "inline"
    response['Content-Disposition'] = f'{disposition}; filename="{receipt.receipt_no}.pdf"'
    return response
