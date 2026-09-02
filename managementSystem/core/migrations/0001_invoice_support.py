from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '__first__'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE invoices
              ADD COLUMN IF NOT EXISTS discount_type varchar(10) NOT NULL DEFAULT 'flat',
              ADD COLUMN IF NOT EXISTS tax_rate numeric(6,2) NOT NULL DEFAULT 0,
              ADD COLUMN IF NOT EXISTS tax_amount numeric(12,2) NOT NULL DEFAULT 0,
              ADD COLUMN IF NOT EXISTS amount_paid numeric(12,2) NOT NULL DEFAULT 0,
              ADD COLUMN IF NOT EXISTS status varchar(20) NOT NULL DEFAULT 'draft',
              ADD COLUMN IF NOT EXISTS payment_terms varchar(50) NOT NULL DEFAULT 'due_on_receipt',
              ADD COLUMN IF NOT EXISTS description text,
              ADD COLUMN IF NOT EXISTS notes text,
              ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
            """,
            reverse_sql="""
            ALTER TABLE invoices
              DROP COLUMN IF EXISTS discount_type,
              DROP COLUMN IF EXISTS tax_rate,
              DROP COLUMN IF EXISTS tax_amount,
              DROP COLUMN IF EXISTS amount_paid,
              DROP COLUMN IF EXISTS status,
              DROP COLUMN IF EXISTS payment_terms,
              DROP COLUMN IF EXISTS description,
              DROP COLUMN IF EXISTS notes,
              DROP COLUMN IF EXISTS created_at;
            """,
        ),
    ]