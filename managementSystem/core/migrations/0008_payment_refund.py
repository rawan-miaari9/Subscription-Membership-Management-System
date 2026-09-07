from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_expense_categories'),
    ]

    operations = [
        migrations.CreateModel(
            name='Payment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('payment_code', models.CharField(blank=True, max_length=50, null=True)),
                ('receipt_no', models.CharField(blank=True, max_length=50, null=True)),
                ('subscription_id', models.IntegerField(blank=True, null=True)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('discount', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('total', models.DecimalField(decimal_places=2, max_digits=12)),
                ('method', models.CharField(blank=True, max_length=20, null=True)),
                ('status', models.CharField(blank=True, max_length=20, null=True)),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'payments',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='Refund',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('refund_code', models.CharField(blank=True, max_length=50, null=True)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('reason', models.TextField(blank=True, null=True)),
                ('status', models.CharField(blank=True, max_length=20, null=True)),
                ('created_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'refunds',
                'managed': False,
            },
        ),
    ]