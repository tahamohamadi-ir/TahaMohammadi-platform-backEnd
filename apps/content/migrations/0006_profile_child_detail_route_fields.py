# Generated manually for P8 detail-route child-row metadata.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0005_profile_availability_profile_long_bio_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="profileskill",
            name="slug",
            field=models.SlugField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="profileskill",
            name="translation_key",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="profileskill",
            name="detail_body",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="profileexperience",
            name="slug",
            field=models.SlugField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="profileexperience",
            name="translation_key",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="profileexperience",
            name="detail_body",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="profileeducation",
            name="slug",
            field=models.SlugField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="profileeducation",
            name="translation_key",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="profileeducation",
            name="detail_body",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="profilepublication",
            name="slug",
            field=models.SlugField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="profilepublication",
            name="translation_key",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="profilepublication",
            name="detail_body",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="profileresearchproject",
            name="slug",
            field=models.SlugField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="profileresearchproject",
            name="translation_key",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="profileresearchproject",
            name="detail_body",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="profilecertificate",
            name="slug",
            field=models.SlugField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="profilecertificate",
            name="translation_key",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="profilecertificate",
            name="detail_body",
            field=models.TextField(blank=True),
        ),
    ]
