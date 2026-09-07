"""Add localized draft/published site settings (PU-03-settings, PRODUCT-V2 §I04)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("siteconfig", "0004_sitesettings_seed_policy"),
    ]

    operations = [
        migrations.CreateModel(
            name="LocalizedSiteSettings",
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
                    "locale",
                    models.CharField(
                        choices=[("fa", "Persian"), ("en", "English")],
                        max_length=10,
                        unique=True,
                    ),
                ),
                (
                    "revision",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "brand_name",
                    models.CharField(blank=True, default="", max_length=200),
                ),
                (
                    "tagline",
                    models.CharField(blank=True, default="", max_length=500),
                ),
                ("footer_text", models.TextField(blank=True, default="")),
                (
                    "seo_title",
                    models.CharField(blank=True, default="", max_length=200),
                ),
                ("seo_description", models.TextField(blank=True, default="")),
                ("nav_links", models.JSONField(blank=True, default=list)),
                ("audience_links", models.JSONField(blank=True, default=list)),
                (
                    "graph_preset",
                    models.CharField(default="atlas-v2", max_length=50),
                ),
                (
                    "portal_preset",
                    models.CharField(default="arch-v2", max_length=50),
                ),
                (
                    "scene_motion",
                    models.CharField(default="full", max_length=20),
                ),
                (
                    "scene_density",
                    models.CharField(default="standard", max_length=20),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("draft", "Draft"), ("published", "Published")],
                        default="draft",
                        max_length=20,
                    ),
                ),
                (
                    "published_payload",
                    models.JSONField(blank=True, default=None, null=True),
                ),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Localized site settings",
                "verbose_name_plural": "Localized site settings",
            },
        ),
    ]
