from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("siteconfig", "0005_localized_product_settings")]
    operations = [
        migrations.AddField(
            model_name="localizedsitesettings", name="managed_copy",
            field=models.JSONField(default=dict, blank=True),
        ),
    ]
