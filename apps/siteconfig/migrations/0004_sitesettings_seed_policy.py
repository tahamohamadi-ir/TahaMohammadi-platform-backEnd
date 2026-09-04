"""Seed policy storage on SiteSettings (BACKEND-211 / ADMIN-281).

The owner seed package's ``supplement/seed-settings.json`` policy was applied
at import time (BACKEND-070) by mutating values, but the policy itself was not
persisted, so the admin could not tell "cleared by owner policy" apart from
"empty value". Storing the raw policy payload makes the seed-managed surface
inspectable in the site settings admin.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("siteconfig", "0003_sitesettings_contact"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="seed_policy",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
