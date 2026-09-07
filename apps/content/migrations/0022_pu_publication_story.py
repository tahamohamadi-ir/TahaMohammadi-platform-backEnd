# Generated for PU-04-publication: attach nullable localized story to publication.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("composition", "0002_compositionpage_kind"),
        ("content", "0021_pu_content_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="publication",
            name="story",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="attached_publications",
                to="composition.compositionpage",
            ),
        ),
    ]
