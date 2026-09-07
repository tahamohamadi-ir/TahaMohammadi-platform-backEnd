# Generated for PU-06-series

import django.db.models.deletion
from django.db import migrations, models


def migrate_existing_series_articles(apps, schema_editor):
    Series = apps.get_model('content', 'Series')
    for series in Series.objects.all():
        if series.members:
            continue
        members = []
        for pos, article in enumerate(series.articles.all().order_by('published_at', 'id')):
            members.append({'family': 'article', 'id': str(article.pk), 'position': pos})
        if members:
            series.members = members
            series.save(update_fields=['members'])


class Migration(migrations.Migration):

    dependencies = [
        ('composition', '0002_compositionpage_kind'),
        ('content', '0029_pu_collection_members'),
    ]

    operations = [
        migrations.AddField(
            model_name='series',
            name='members',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Ordered series members: [{family: 'article', id, position}].",
            ),
        ),
        migrations.AddField(
            model_name='series',
            name='story',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='attached_series',
                to='composition.compositionpage',
            ),
        ),
        migrations.RunPython(migrate_existing_series_articles, reverse_code=migrations.RunPython.noop),
    ]
