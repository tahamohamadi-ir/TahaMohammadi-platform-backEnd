"""Add owner-selected featured references and brand media to localized drafts."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("siteconfig", "0006_managed_copy"),
        ("media", "0003_media_presentation_metadata"),
    ]
    operations = [
        migrations.AddField(
            model_name="localizedsitesettings",
            name="featured_records",
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.AddField(
            model_name="localizedsitesettings",
            name="brand_media",
            field=models.ForeignKey(
                to="media.media", null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL, related_name="+",
            ),
        ),
    ]
