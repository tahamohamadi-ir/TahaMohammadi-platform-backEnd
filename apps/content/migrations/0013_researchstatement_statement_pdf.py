"""Additive nullable statement_pdf Media FK on ResearchStatement (DEFER-0019).

Owner: dumpdata + backup before production migrate. Never enable
``CMS_CD_AUTO_MIGRATE``.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0012_richtext_to_textfield"),
        ("media", "0002_media_alt_text_en_media_alt_text_fa"),
    ]

    operations = [
        migrations.AddField(
            model_name="researchstatement",
            name="statement_pdf",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional downloadable PDF (Media library; public only when active).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="media.media",
            ),
        ),
    ]
