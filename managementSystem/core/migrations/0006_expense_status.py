from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_financial'),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE expenses ADD COLUMN status varchar(20) NULL;",
            reverse_sql="ALTER TABLE expenses DROP COLUMN status;",
        ),
    ]