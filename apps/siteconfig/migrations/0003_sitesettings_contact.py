"""Public contact block on SiteSettings (board A10, owner decision 2026-08-23)."""

from django.db import migrations, models


def seed_owner_contact(apps, schema_editor):
    """Load the owner-provided public contact values (intended for publication)."""
    SiteSettings = apps.get_model("siteconfig", "SiteSettings")
    SiteSettings.objects.update_or_create(
        site_key="default",
        defaults={
            "contact_email": "taha.mohammadi@shahed.ac.ir",
            "contact_phone": "+98 910 235 5374",
            "contact_phone_intl": "+1 925 456 4581",
            "contact_location": "Tehran, Iran",
            "contact_linkedin": "https://linkedin.com/in/taha-mohammadi-95770986",
            "contact_orcid": "https://orcid.org/0009-0006-7736-7638",
            "contact_employer": "MCI (Hamrah-e Aval)",
            "contact_employer_url": "https://mci.ir",
            "contact_form_enabled": True,
        },
    )


def unseed_owner_contact(apps, schema_editor):
    SiteSettings = apps.get_model("siteconfig", "SiteSettings")
    SiteSettings.objects.update_or_create(
        site_key="default",
        defaults={
            "contact_email": "",
            "contact_phone": "",
            "contact_phone_intl": "",
            "contact_location": "",
            "contact_linkedin": "",
            "contact_orcid": "",
            "contact_employer": "",
            "contact_employer_url": "",
            "contact_form_enabled": False,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("siteconfig", "0002_sitesettings_current_cv_resume_media"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="contact_email",
            field=models.CharField(blank=True, default="", max_length=254),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="contact_phone",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="contact_phone_intl",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="contact_location",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="contact_linkedin",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="contact_orcid",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="contact_employer",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="contact_employer_url",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="contact_form_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(seed_owner_contact, unseed_owner_contact),
    ]
