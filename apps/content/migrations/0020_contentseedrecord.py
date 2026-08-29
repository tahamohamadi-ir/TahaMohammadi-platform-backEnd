"""ContentSeedRecord storage for owner content seed v1.1 import."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0019_graph_storage"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContentSeedRecord",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "content_id",
                    models.CharField(db_index=True, max_length=200, unique=True),
                ),
                ("content_type", models.CharField(db_index=True, max_length=64)),
                ("locale", models.CharField(db_index=True, max_length=8)),
                ("slug", models.SlugField(max_length=200)),
                ("title", models.CharField(max_length=300)),
                ("approval_state", models.CharField(max_length=32)),
                ("publication_state", models.CharField(max_length=32)),
                ("translation_state", models.CharField(max_length=32)),
                ("visibility", models.CharField(max_length=32)),
                ("payload", models.JSONField()),
                (
                    "package_version",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "mapped_model_label",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                ("mapped_object_id", models.PositiveIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "content_seed_record",
                "ordering": ["content_id"],
                "indexes": [
                    models.Index(
                        fields=["content_type", "locale"],
                        name="seed_record_type_locale_idx",
                    )
                ],
            },
        ),
    ]
