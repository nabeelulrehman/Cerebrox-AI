from datetime import timedelta

from django.db import migrations, models


def populate_scheduled_end(apps, schema_editor):
    LearningSession = apps.get_model('preparation', 'LearningSession')
    for session in LearningSession.objects.all().iterator():
        session.scheduled_end_at = session.scheduled_at + timedelta(minutes=session.duration_minutes)
        session.save(update_fields=['scheduled_end_at'])


class Migration(migrations.Migration):
    dependencies = [('preparation', '0005_preparationtopic_learning_topic')]

    operations = [
        migrations.AddField(
            model_name='learningsession', name='scheduled_end_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(populate_scheduled_end, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='learningsession', name='scheduled_end_at',
            field=models.DateTimeField(),
        ),
    ]
