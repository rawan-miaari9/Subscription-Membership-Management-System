from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_receipts'),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE receipts ADD COLUMN logo text NULL;",
            reverse_sql="ALTER TABLE receipts DROP COLUMN logo;",
        ),
    ]
