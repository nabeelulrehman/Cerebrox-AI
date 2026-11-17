from django.db import migrations


def link_topics(apps, schema_editor):
    PreparationTopic = apps.get_model('preparation', 'PreparationTopic')
    Course = apps.get_model('learning', 'Course')
    Topic = apps.get_model('learning', 'Topic')
    for prep_topic in PreparationTopic.objects.filter(learning_topic__isnull=True).select_related('plan__student').iterator():
        name = prep_topic.name.strip()
        if not name or len(name) > 150:
            continue
        student_id = prep_topic.plan.student_id
        learning_topic = Topic.objects.filter(course__student_id=student_id, name__iexact=name).first()
        if learning_topic is None:
            course, _ = Course.objects.get_or_create(
                student_id=student_id, name='Exam Preparation',
                defaults={'description': 'Topics used by personalized exam preparation plans.'},
            )
            learning_topic, _ = Topic.objects.get_or_create(
                course=course, name=name, defaults={'description': prep_topic.description},
            )
        prep_topic.learning_topic_id = learning_topic.pk
        prep_topic.save(update_fields=['learning_topic'])


class Migration(migrations.Migration):
    dependencies = [('preparation', '0006_learningsession_scheduled_end_at')]

    operations = [migrations.RunPython(link_topics, migrations.RunPython.noop)]
