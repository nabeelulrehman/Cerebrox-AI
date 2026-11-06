from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_existing_replies(apps, schema_editor):
    GuidanceRequest = apps.get_model('marketplace', 'GuidanceRequest')
    GuidanceMessage = apps.get_model('marketplace', 'GuidanceMessage')
    for conversation in GuidanceRequest.objects.exclude(reply='').iterator():
        message = GuidanceMessage.objects.create(
            conversation_id=conversation.pk,
            sender_id=conversation.teacher_id,
            body=conversation.reply,
        )
        GuidanceMessage.objects.filter(pk=message.pk).update(sent_at=conversation.created_at)


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='guidancerequest',
            name='subject',
            field=models.CharField(blank=True, default='Guidance request', max_length=160),
        ),
        migrations.AddField(
            model_name='guidancerequest',
            name='started_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='started_guidance_requests',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name='GuidanceMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('body', models.TextField(max_length=2000)),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
                ('conversation', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='messages',
                    to='marketplace.guidancerequest',
                )),
                ('sender', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='guidance_messages',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['sent_at', 'pk'],
            },
        ),
        migrations.RunPython(migrate_existing_replies, migrations.RunPython.noop),
    ]
