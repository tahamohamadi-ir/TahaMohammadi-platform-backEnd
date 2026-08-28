"""Convert remaining RichTextField columns to TextField (same DB type).

Additive / reversible AlterField only. Stored HTML bytes are unchanged.
After DEBT-0003 historical rewrites, ``0002``/``0003``/``0004`` already use
TextField; this migration remains for production DBs that applied the old
RichTextField definitions. Owner dumpdata + backup before production migrate
(RISK-0010). Never enable ``CMS_CD_AUTO_MIGRATE``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0011_rewire_image_fks_to_media"),
    ]

    operations = [
        migrations.AlterField(
            model_name="article",
            name="body",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="researchstatement",
            name="body",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="projectcasestudydetails",
            name="technical_decisions",
            field=models.TextField(blank=True),
        ),
    ]
