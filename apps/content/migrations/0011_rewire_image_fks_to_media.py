"""Historical rewire of featured/diagram/screenshot FKs to Media.

DEBT-0003 CLOSED path: earlier migrations ``0002``/``0004`` now create Media
FKs directly (no ``wagtailimages`` dependency), so this migration is a no-op
for fresh installs. Production databases that already applied the original
data-copy operations keep their ``django_migrations`` row and are not re-run.

Owner dumpdata + backup before any production migrate remains RISK-0010.
Never enable ``CMS_CD_AUTO_MIGRATE``.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0010_entity_stories"),
        ("media", "0002_media_alt_text_en_media_alt_text_fa"),
    ]

    operations = [
        # Intentionally empty: schema already matches Media FKs via rewritten
        # historical 0002/0004. Original RunPython + RenameField lived here.
    ]
