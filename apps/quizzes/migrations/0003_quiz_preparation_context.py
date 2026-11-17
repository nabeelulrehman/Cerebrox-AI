import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('preparation', '0006_learningsession_scheduled_end_at'),
        ('quizzes', '0002_quiz_topics'),
    ]

    operations = [
        migrations.AddField(
            model_name='quiz', name='preparation_plan',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='quiz_attempts', to='preparation.preparationplan'),
        ),
        migrations.AddField(
            model_name='quiz', name='preparation_topic',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='quiz_attempts', to='preparation.preparationtopic'),
        ),
        migrations.AddField(
            model_name='quiz', name='preparation_session',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='quiz_attempts', to='preparation.learningsession'),
        ),
    ]
