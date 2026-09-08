from decimal import Decimal
from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth.hashers import make_password
from django.db import connection

class Command(BaseCommand):
    help = 'Seed clean real data (no mock) for all tables'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Delete existing data before seeding')

    def handle(self, *args, **options):
        from core.models import User, Member, MembershipPlan, Subscription, Payment, Attendance, Invoice, Receipt, Service, PlanService, FinancialSetting, BusinessInformation, PricingConfig, Promotion
        from django.db import transaction

        if options['reset']:
            self.stdout.write("Resetting data...")
            with connection.cursor() as cur:
                # Delete in FK order
                for tbl in ['plan_services','services','attendance','payments','receipts','invoices','subscriptions','members','membership_plans','promotions','pricing_config']:
                    try:
                        cur.execute(f'DELETE FROM "{tbl}"')
                        self.stdout.write(f"  cleared {tbl}")
                    except Exception as e:
                        self.stdout.write(f"  skip {tbl}: {e}")
                # Keep users but reset to 3
                try:
                    cur.execute("DELETE FROM users WHERE id > 3")
                except Exception:
                    pass
                # Fix sequences
                for seq, tbl in [('members_id_seq','members'),('membership_plans_id_seq','membership_plans'),('subscriptions_id_seq','subscriptions'),('payments_id_seq','payments')]:
                    try:
                        cur.execute(f"SELECT setval('{seq}', COALESCE((SELECT MAX(id) FROM {tbl}),0)+1, false)")
                    except Exception:
                        pass

        # Users: ensure 3
        users_data = [
            (1, "Admin", "admin@ironcore.gym", "admin", "Admin"),
            (2, "Accountant User", "accountant@ironcore.gym", "accountant", "Accountant"),
            (3, "Mahmoud Audi", "mahmoud@test.com", "mahmoudaudi", "Admin"),
        ]
        for uid, full_name, email, username, role in users_data:
            u, created = User.objects.get_or_create(id=uid, defaults={
                'full_name': full_name, 'email': email, 'username': username,
                'password': make_password('admin123'), 'role': role, 'status': 'Active'
            })
            if created:
                self.stdout.write(f"Created user {username}")
            else:
                # Ensure role/status correct
                if u.role != role or u.status != 'Active':
                    u.role = role
                    u.status = 'Active'
                    u.save(update_fields=['role','status'])

        # Plans: 5
        plans_data = [
            ("Basic", 30, Decimal('30.00'), True),
            ("Silver", 30, Decimal('40.00'), True),
            ("Premium", 30, Decimal('60.00'), True),
            ("Gold", 90, Decimal('110.00'), True),
            ("Annual", 365, Decimal('400.00'), True),
        ]
        plans = {}
        for name, days, price, fixed in plans_data:
            p, created = MembershipPlan.objects.get_or_create(name=name, defaults={
                'slug': name.lower(), 'duration_days': days, 'price': price, 'is_fixed': fixed, 'is_active': True
            })
            if not created and (p.price != price or p.duration_days != days):
                p.price = price
                p.duration_days = days
                p.save(update_fields=['price','duration_days'])
            plans[name] = p
            self.stdout.write(f"{'Created' if created else 'Exists'} plan {name} ${price}")

        # Services: 4
        services_data = [
            ("SRV-001", "Pool Access", "Unlimited pool access"),
            ("SRV-002", "Personal Training", "1 session per week"),
            ("SRV-003", "Locker Rental", "Monthly locker"),
            ("SRV-004", "Sauna", "Unlimited sauna"),
        ]
        from core.models import Service
        for code, name, desc in services_data:
            s, created = Service.objects.get_or_create(service_code=code, defaults={'name': name, 'description': desc, 'is_active': True})
            self.stdout.write(f"{'Created' if created else 'Exists'} service {name}")

        # Members: 10 clean
        members_data = [
            ("Ahmed Hassan", "ahmed@test.com", "555-0101", "active"),
            ("Sara Ali", "sara@test.com", "555-0102", "active"),
            ("David Cohen", "david@test.com", "555-0103", "active"),
            ("Layla Mahmoud", "layla@test.com", "555-0104", "expiring"),
            ("Omar Khalil", "omar@test.com", "555-0105", "suspended"),
            ("Nour Hassan", "nour@test.com", "555-0106", "active"),
            ("Khaled Youssef", "khaled@test.com", "555-0107", "active"),
            ("Rania Samir", "rania@test.com", "555-0108", "active"),
            ("Youssef Ali", "youssef@test.com", "555-0109", "expired"),
            ("Mona Adel", "mona@test.com", "555-0110", "active"),
        ]
        members = []
        for idx, (full_name, email, phone, status) in enumerate(members_data, start=1):
            m, created = Member.objects.get_or_create(email=email, defaults={
                'member_code': f"MBR-{100+idx:04d}",
                'full_name': full_name,
                'phone': phone,
                'join_date': date.today() - timedelta(days=30+idx*5),
                'status': status,
                'balance': Decimal('0.00'),
                'initials': ''.join([w[0] for w in full_name.split()[:2]]).upper(),
            })
            members.append(m)
            self.stdout.write(f"{'Created' if created else 'Exists'} member {m.member_code} {full_name} {status}")

        # Subscriptions: 10 (one per member, with realistic dates)
        subs = []
        plan_cycle = list(plans.values())
        for idx, member in enumerate(members):
            plan = plan_cycle[idx % len(plan_cycle)]
            start = date.today() - timedelta(days=20-idx)
            end = start + timedelta(days=plan.duration_days)
            # Status based on member status and end date
            if member.status == 'suspended':
                status = 'suspended'
            elif member.status == 'expired':
                status = 'expired'
                end = date.today() - timedelta(days=5)
            elif end <= date.today() + timedelta(days=7):
                status = 'expiring'
            else:
                status = 'active'
            sub, created = Subscription.objects.get_or_create(
                member=member,
                defaults={
                    'subscription_code': f"SUB-{member.member_code}-0001",
                    'plan': plan,
                    'start_date': start,
                    'end_date': end,
                    'status': status,
                    'auto_renew': True,
                }
            )
            if not created:
                # Update to ensure clean
                sub.plan = plan
                sub.start_date = start
                sub.end_date = end
                sub.status = status
                sub.save(update_fields=['plan','start_date','end_date','status'])
            subs.append(sub)
            self.stdout.write(f"{'Created' if created else 'Exists'} sub {sub.subscription_code} {member.full_name} {plan.name} {status}")

        # Payments: 10 (one per member, real amounts)
        for idx, member in enumerate(members):
            sub = subs[idx]
            amount = sub.plan.price if sub.plan else Decimal('40.00')
            # For some, make partial
            if idx % 3 == 1:
                amount = (amount * Decimal('0.5')).quantize(Decimal('0.01'))
                status = 'pending'
            else:
                status = 'success'
            pay, created = Payment.objects.get_or_create(
                member=member,
                subscription=sub,
                defaults={
                    'payment_code': f"PAY-{200+idx:04d}",
                    'receipt_no': f"RCPT-{200+idx:04d}",
                    'amount': amount,
                    'discount': Decimal('0.00'),
                    'total': amount,
                    'method': ['cash','card','bank_transfer'][idx % 3],
                    'status': status,
                    'paid_at': timezone.now() - timedelta(days=idx),
                }
            )
            self.stdout.write(f"{'Created' if created else 'Exists'} payment {pay.payment_code} {member.full_name} ${amount} {status}")

        # Attendance: 15 records for last 5 days, 3 members per day (raw SQL to avoid generated column)
        for day_offset in range(5):
            d = date.today() - timedelta(days=day_offset)
            for mi in range(3):
                member = members[(day_offset*3 + mi) % len(members)]
                if member.status == 'suspended':
                    continue
                check_in = (timezone.localtime() - timedelta(hours=2)).time()
                check_out = (timezone.localtime() - timedelta(hours=1)).time() if day_offset % 2 == 0 else None
                try:
                    with connection.cursor() as cur:
                        if check_out:
                            cur.execute(
                                "INSERT INTO attendance (member_id, date, check_in, check_out) VALUES (%s, %s, %s, %s) ON CONFLICT (member_id, date) DO NOTHING",
                                [member.id, d, check_in, check_out]
                            )
                            if cur.rowcount == 0:
                                cur.execute("UPDATE attendance SET check_out = %s WHERE member_id = %s AND date = %s AND check_out IS NULL", [check_out, member.id, d])
                        else:
                            cur.execute(
                                "INSERT INTO attendance (member_id, date, check_in) VALUES (%s, %s, %s) ON CONFLICT (member_id, date) DO NOTHING",
                                [member.id, d, check_in]
                            )
                    self.stdout.write(f"Created/Exists attendance {member.full_name} {d}")
                except Exception as e:
                    self.stdout.write(f"Skip attendance {member.full_name} {d}: {e}")

        # Invoices: 5
        from core.models import Invoice
        for idx, member in enumerate(members[:5]):
            inv, created = Invoice.objects.get_or_create(invoice_no=f"INV-2026-{300+idx:04d}", defaults={
                'member': member,
                'bill_to': member.full_name,
                'description': f"{plans[list(plans.keys())[idx%len(plans)]].name} Membership",
                'subtotal': Decimal('100.00') + idx*10,
                'discount_type': 'flat',
                'discount': Decimal('5.00'),
                'tax_rate': Decimal('14.00'),
                'status': 'paid' if idx % 2 == 0 else 'sent',
                'payment_terms': 'due_on_receipt',
                'issued_date': date.today() - timedelta(days=idx*2),
                'due_date': date.today() + timedelta(days=10-idx),
                'amount_paid': Decimal('100.00') if idx % 2 == 0 else Decimal('0.00'),
            })
            inv.recalculate()
            inv.save()
            self.stdout.write(f"{'Created' if created else 'Exists'} invoice {inv.invoice_no}")

        # Receipts: 2
        from core.models import Receipt
        for idx, member in enumerate(members[:2]):
            rcpt, created = Receipt.objects.get_or_create(receipt_no=f"RCPT-2026-{400+idx:04d}", defaults={
                'member': member,
                'bill_to': member.full_name,
                'description': "Membership Payment",
                'subtotal': Decimal('40.00'),
                'discount_type': 'flat',
                'discount': Decimal('0.00'),
                'tax_rate': Decimal('14.00'),
                'method': 'cash',
                'paid_date': date.today() - timedelta(days=idx),
            })
            rcpt.recalculate()
            rcpt.save()
            self.stdout.write(f"{'Created' if created else 'Exists'} receipt {rcpt.receipt_no}")

        # PricingConfig
        from core.models import PricingConfig
        cfg = PricingConfig.get_singleton()
        self.stdout.write(f"Pricing: {cfg.base_price} {cfg.billing_cycle}")

        # Promotions
        from core.models import Promotion
        for code, typ, val in [("SAVE10","percent",Decimal("10.00")),("WELCOME20","flat",Decimal("20.00"))]:
            p, created = Promotion.objects.get_or_create(code=code, defaults={'discount_type': typ, 'discount_value': val, 'is_active': True})
            self.stdout.write(f"{'Created' if created else 'Exists'} promotion {code}")

        self.stdout.write(self.style.SUCCESS("Seed clean done - all real, no mock"))
