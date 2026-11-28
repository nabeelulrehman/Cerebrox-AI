from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('marketplace', '0019_permanent_session_deletion'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentmethodconfiguration',
            name='jazzcash_account_title',
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name='paymentmethodconfiguration',
            name='jazzcash_account_number',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name='paymentmethodconfiguration',
            name='easypaisa_account_title',
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name='paymentmethodconfiguration',
            name='easypaisa_account_number',
            field=models.CharField(blank=True, max_length=40),
        ),
    ]
