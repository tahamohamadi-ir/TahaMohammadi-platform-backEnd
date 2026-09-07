# Generated for PU-04-creative: attach nullable localized story to creative-work.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("composition", "0002_compositionpage_kind"),
        ("content", "0023_pu_course_story"),
    ]

    operations = [
        migrations.AddField(
            model_name="creativework",
            name="story",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="attached_creative_works",
                to="composition.compositionpage",
            ),
        ),
    ]
