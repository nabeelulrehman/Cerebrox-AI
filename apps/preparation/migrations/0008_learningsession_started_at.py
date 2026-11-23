from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('preparation', '0007_link_existing_preparation_topics')]

    operations = [
        migrations.AddField(
            model_name='learningsession',
            name='started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
