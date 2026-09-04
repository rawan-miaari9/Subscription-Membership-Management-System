from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_payment_refund'),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationSetting',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(blank=True, max_length=50, null=True)),
                ('name', models.CharField(blank=True, max_length=150, null=True)),
                ('description', models.TextField(blank=True, null=True)),
                ('enabled', models.BooleanField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'core_notificationsetting',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='NotificationRead',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nkey', models.CharField(max_length=120, unique=True)),
                ('read_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'app_notificationread',
                'managed': False,
            },
        ),
        migrations.RunSQL(
            sql=(
                "CREATE TABLE IF NOT EXISTS app_notificationread ("
                "id bigserial PRIMARY KEY, "
                "nkey varchar(120) NOT NULL UNIQUE, "
                "read_at timestamp with time zone NOT NULL DEFAULT now());"
            ),
            reverse_sql="DROP TABLE IF EXISTS app_notificationread;",
        ),
    ]