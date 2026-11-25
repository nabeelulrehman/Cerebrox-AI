from django.db import migrations, models
import django.db.models.deletion


def snapshot_session_titles(apps, schema_editor):
    Payment = apps.get_model('marketplace', 'Payment')
    db = schema_editor.connection.alias
    for payment_id, title in Payment.objects.using(db).filter(
        session__isnull=False, session_title='',
    ).values_list('pk', 'session__title').iterator():
        Payment.objects.using(db).filter(pk=payment_id).update(session_title=title)


class Migration(migrations.Migration):
    dependencies = [
        ('marketplace', '0018_teachingsession_archived_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='session_title',
            field=models.CharField(blank=True, default='', max_length=160),
        ),
        migrations.RemoveConstraint(
            model_name='payment',
            name='payment_has_target',
        ),
        migrations.AlterField(
            model_name='payment',
            name='session',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='payments', to='marketplace.teachingsession'),
        ),
        migrations.RunPython(snapshot_session_titles, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='teachingsession',
            name='archived_at',
        ),
    ]
