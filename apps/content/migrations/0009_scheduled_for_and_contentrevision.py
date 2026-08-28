# Generated manually for DEBT-0005 (scheduled publish + content revisions).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


LIFECYCLE_CHOICES = [
    ("draft", "Draft"),
    ("review", "Review"),
    ("scheduled", "Scheduled"),
    ("published", "Published"),
    ("archived", "Archived"),
]

SCHEDULED_FOR_MODELS = [
    "article",
    "landing",
    "profile",
    "project",
    "publication",
    "researchstatement",
    "researchtopic",
    "series",
]


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0008_article_story"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        *[
            migrations.AddField(
                model_name=name,
                name="scheduled_for",
                field=models.DateTimeField(blank=True, db_index=True, null=True),
            )
            for name in SCHEDULED_FOR_MODELS
        ],
        *[
            migrations.AlterField(
                model_name=name,
                name="status",
                field=models.CharField(
                    choices=LIFECYCLE_CHOICES,
                    db_index=True,
                    default="draft",
                    max_length=20,
                ),
            )
            for name in SCHEDULED_FOR_MODELS
        ],
        migrations.CreateModel(
            name="ContentRevision",
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
                ("entity_key", models.CharField(db_index=True, max_length=64)),
                ("object_id", models.PositiveIntegerField(db_index=True)),
                ("snapshot", models.JSONField()),
                ("note", models.CharField(blank=True, max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="content_revisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "content_revision",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="contentrevision",
            index=models.Index(
                fields=["entity_key", "object_id", "-created_at"],
                name="content_rev_entity_obj_created",
            ),
        ),
    ]
