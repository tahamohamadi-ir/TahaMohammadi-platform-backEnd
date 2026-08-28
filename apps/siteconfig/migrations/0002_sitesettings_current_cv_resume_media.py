# Generated manually for ADM-6 DEFER-0029 / DEBT-0006 CV current-document.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("media", "0002_media_alt_text_en_media_alt_text_fa"),
        ("siteconfig", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="current_cv_media",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="media.media",
            ),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="current_resume_media",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="media.media",
            ),
        ),
    ]
