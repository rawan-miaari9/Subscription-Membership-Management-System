from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_expense_status'),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE expenses DROP CONSTRAINT IF EXISTS expenses_category_check;"
                "ALTER TABLE expenses ADD CONSTRAINT expenses_category_check "
                "CHECK (category = ANY (ARRAY['rent'::varchar, 'equipment'::varchar, "
                "'salaries'::varchar, 'utilities'::varchar, 'maintenance'::varchar, "
                "'operations'::varchar, 'other'::varchar]));"
            ),
            reverse_sql=(
                "ALTER TABLE expenses DROP CONSTRAINT IF EXISTS expenses_category_check;"
                "ALTER TABLE expenses ADD CONSTRAINT expenses_category_check "
                "CHECK (category = ANY (ARRAY['equipment'::varchar, 'maintenance'::varchar, "
                "'salary'::varchar, 'utility'::varchar, 'other'::varchar]));"
            ),
        ),
    ]