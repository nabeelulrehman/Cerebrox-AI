from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0017_remove_payment_payment_has_target_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='teachingsession',
            name='archived_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
