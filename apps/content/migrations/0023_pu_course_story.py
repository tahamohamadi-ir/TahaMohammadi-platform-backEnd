# Generated for PU-04-course: attach nullable localized story to course.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("composition", "0002_compositionpage_kind"),
        ("content", "0022_pu_publication_story"),
    ]

    operations = [
        migrations.AddField(
            model_name="course",
            name="story",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="attached_courses",
                to="composition.compositionpage",
            ),
        ),
    ]
