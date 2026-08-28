"""Profile seed import tests."""

import json

import pytest
from django.core.management import call_command

from apps.content.models import Locale, Profile, ProfileResearchProject


@pytest.mark.django_db
def test_import_profile_seed_creates_linked_locales(tmp_path):
    seed = {
        "translationKey": "22222222-2222-2222-2222-222222222222",
        "profiles": {
            "en": {
                "shortBio": "Short bio",
                "longBio": "Long bio",
                "skills": [{"category": "Programming", "name": "Python", "source": "Seed"}],
                "experience": [],
                "education": [],
                "publications": [],
                "researchProjects": [
                    {
                        "title": "Sample",
                        "summary": "Summary",
                        "slug": "sample-research",
                        "translationKey": "44444444-4444-4444-4444-444444444444",
                        "detailBody": "Detail body paragraph.",
                    }
                ],
                "certificates": [],
                "socials": [{"platform": "GitHub", "url": "https://github.com/example"}],
                "availability": "Available",
            },
            "fa": {
                "shortBio": "بیو کوتاه",
                "longBio": "بیو بلند",
                "skills": [{"category": "برنامه‌نویسی", "name": "Python", "source": "Seed"}],
                "experience": [],
                "education": [],
                "publications": [],
                "researchProjects": [
                    {
                        "title": "Sample",
                        "summary": "Summary",
                        "slug": "sample-research",
                        "translationKey": "44444444-4444-4444-4444-444444444444",
                        "detailBody": "Detail body paragraph.",
                    }
                ],
                "certificates": [],
                "socials": [{"platform": "GitHub", "url": "https://github.com/example"}],
                "availability": "در دسترس",
            },
        },
    }
    seed_path = tmp_path / "profile.seed.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")

    call_command("import_profile_seed", path=str(seed_path))

    en = Profile.objects.get(locale=Locale.EN, slug="about")
    fa = Profile.objects.get(locale=Locale.FA, slug="about")
    assert en.translation_key == fa.translation_key
    assert en.revision == 1
    assert en.skills.count() == 1
    assert fa.social_links.count() == 1
    research = ProfileResearchProject.objects.get(profile=en, slug="sample-research")
    assert str(research.translation_key) == "44444444-4444-4444-4444-444444444444"
    assert research.detail_body == "Detail body paragraph."
