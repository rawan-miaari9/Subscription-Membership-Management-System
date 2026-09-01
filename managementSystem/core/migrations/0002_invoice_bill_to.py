from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_invoice_support'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE invoices
              ADD COLUMN IF NOT EXISTS bill_to varchar(255);
            """,
            reverse_sql="""
            ALTER TABLE invoices
              DROP COLUMN IF EXISTS bill_to;
            """,
        ),
    ]