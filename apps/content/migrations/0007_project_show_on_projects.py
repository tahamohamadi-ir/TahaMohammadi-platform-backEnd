# Additive listing flag for the public /projects/ catalog (ADM-6).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0006_profile_child_detail_route_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="show_on_projects",
            field=models.BooleanField(
                db_default=True,
                default=True,
                help_text="When false, a published project is omitted from the public /projects/ list.",
            ),
        ),
    ]
