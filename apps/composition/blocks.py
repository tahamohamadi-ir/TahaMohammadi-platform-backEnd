"""Typed block catalog + fail-closed validators (ADR-0026, ADM-3).

Landing pages keep the bilingual catalog. Story documents use a separate
single-locale catalog (figure/video/audio/math). Unknown types and unknown
setting keys are rejected rather than silently dropped (fail-closed).
"""

from __future__ import annotations

from apps.media.models import Media
from apps.media.sniff import mime_family

KIND_LANDING = "landing"
KIND_STORY = "story"
VALID_KINDS = (KIND_LANDING, KIND_STORY)

LANDING_BLOCK_TYPES: list[str] = [
    "hero",
    "heading",
    "text",
    "quote",
    "cta",
    "gallery",
    "divider",
]
STORY_BLOCK_TYPES: list[str] = [
    "heading",
    "text",
    "quote",
    "cta",
    "figure",
    "gallery",
    "video",
    "audio",
    "math",
    "accordion",
    "tabs",
    "timeline",
    "counters",
    "before_after",
    "slider",
    "divider",
]
# Backward-compatible alias: landing catalog (existing admin tests).
BLOCK_TYPES: list[str] = list(LANDING_BLOCK_TYPES)

BLOCK_TYPE_LABELS_FA: dict[str, str] = {
    "hero": "هرا",
    "heading": "عنوان",
    "text": "متن",
    "quote": "نقلقول",
    "cta": "فراخوان",
    "gallery": "گالری",
    "divider": "جداکننده",
    "figure": "تصویر",
    "video": "ویدیو",
    "audio": "صوت",
    "math": "فرمول",
    "accordion": "آکاردئون",
    "tabs": "زبانه‌ها",
    "timeline": "خط زمانی",
    "counters": "شمارنده‌ها",
    "before_after": "قبل/بعد",
    "slider": "اسلایدر",
}

LANDING_BLOCK_FIELD_SPECS: list[dict] = [
    {
        "type": "hero",
        "required": ["titleFa", "titleEn", "leadFa", "leadEn"],
        "fields": [
            {"key": "titleFa", "label": "عنوان (فارسی)", "type": "text"},
            {"key": "titleEn", "label": "عنوان (انگلیسی)", "type": "text"},
            {"key": "leadFa", "label": "مقدمه (فارسی)", "type": "textarea"},
            {"key": "leadEn", "label": "مقدمه (انگلیسی)", "type": "textarea"},
            {
                "key": "align",
                "label": "تراز",
                "type": "select",
                "options": ["left", "center", "right"],
            },
            {"key": "mediaId", "label": "تصویر", "type": "media"},
        ],
    },
    {
        "type": "heading",
        "required": ["textFa", "textEn"],
        "fields": [
            {"key": "textFa", "label": "متن (فارسی)", "type": "text"},
            {"key": "textEn", "label": "متن (انگلیسی)", "type": "text"},
            {"key": "level", "label": "سطح", "type": "select", "options": ["h2", "h3", "h4"]},
        ],
    },
    {
        "type": "text",
        "required": ["bodyFa", "bodyEn"],
        "fields": [
            {"key": "bodyFa", "label": "متن (فارسی)", "type": "textarea"},
            {"key": "bodyEn", "label": "متن (انگلیسی)", "type": "textarea"},
        ],
    },
    {
        "type": "quote",
        "required": ["bodyFa", "bodyEn", "sourceFa", "sourceEn"],
        "fields": [
            {"key": "bodyFa", "label": "متن (فارسی)", "type": "textarea"},
            {"key": "bodyEn", "label": "متن (انگلیسی)", "type": "textarea"},
            {"key": "sourceFa", "label": "منبع (فارسی)", "type": "text"},
            {"key": "sourceEn", "label": "منبع (انگلیسی)", "type": "text"},
        ],
    },
    {
        "type": "cta",
        "required": ["labelFa", "labelEn"],
        "fields": [
            {"key": "labelFa", "label": "برچسب (فارسی)", "type": "text"},
            {"key": "labelEn", "label": "برچسب (انگلیسی)", "type": "text"},
            {"key": "url", "label": "پیوند", "type": "text"},
            {"key": "style", "label": "سبک", "type": "select", "options": ["primary", "secondary"]},
        ],
    },
    {
        "type": "gallery",
        "required": ["mediaIds"],
        "fields": [
            {"key": "mediaIds", "label": "تصاویر", "type": "mediaList"},
        ],
    },
    {
        "type": "divider",
        "required": [],
        "fields": [],
    },
]

STORY_BLOCK_FIELD_SPECS: list[dict] = [
    {
        "type": "heading",
        "required": ["text"],
        "fields": [
            {"key": "text", "label": "متن", "type": "text"},
            {"key": "level", "label": "سطح", "type": "select", "options": ["h2", "h3", "h4"]},
        ],
    },
    {
        "type": "text",
        "required": ["body"],
        "fields": [
            {"key": "body", "label": "متن", "type": "textarea"},
        ],
    },
    {
        "type": "quote",
        "required": ["body", "source"],
        "fields": [
            {"key": "body", "label": "متن", "type": "textarea"},
            {"key": "source", "label": "منبع", "type": "text"},
        ],
    },
    {
        "type": "cta",
        "required": ["label"],
        "fields": [
            {"key": "label", "label": "برچسب", "type": "text"},
            {"key": "url", "label": "پیوند", "type": "text"},
            {"key": "style", "label": "سبک", "type": "select", "options": ["primary", "secondary"]},
        ],
    },
    {
        "type": "figure",
        "required": ["mediaId"],
        "fields": [
            {"key": "mediaId", "label": "تصویر", "type": "media"},
            {"key": "caption", "label": "شرح", "type": "text"},
        ],
        "mediaFamily": "image",
    },
    {
        "type": "gallery",
        "required": ["mediaIds"],
        "fields": [
            {"key": "mediaIds", "label": "تصاویر", "type": "mediaList"},
        ],
        "mediaFamily": "image",
    },
    {
        "type": "video",
        "required": ["mediaId"],
        "fields": [
            {"key": "mediaId", "label": "ویدیو", "type": "media"},
            {"key": "caption", "label": "شرح", "type": "text"},
        ],
        "mediaFamily": "video",
    },
    {
        "type": "audio",
        "required": ["mediaId"],
        "fields": [
            {"key": "mediaId", "label": "صوت", "type": "media"},
            {"key": "caption", "label": "شرح", "type": "text"},
        ],
        "mediaFamily": "audio",
    },
    {
        "type": "math",
        "required": ["html"],
        "fields": [
            {"key": "html", "label": "MathML یا متن", "type": "textarea"},
        ],
    },
    {
        "type": "accordion",
        "required": ["items"],
        "fields": [
            {
                "key": "items",
                "label": "آیتم‌ها",
                "type": "itemList",
                "minItems": 1,
                "maxItems": 12,
                "itemFields": [
                    {"key": "title", "label": "عنوان", "type": "text", "required": True},
                    {"key": "body", "label": "متن", "type": "textarea", "required": True},
                ],
            },
        ],
    },
    {
        "type": "tabs",
        "required": ["items"],
        "fields": [
            {
                "key": "items",
                "label": "زبانه‌ها",
                "type": "itemList",
                "minItems": 2,
                "maxItems": 8,
                "itemFields": [
                    {"key": "label", "label": "برچسب", "type": "text", "required": True},
                    {"key": "body", "label": "متن", "type": "textarea", "required": True},
                ],
            },
        ],
    },
    {
        "type": "timeline",
        "required": ["items"],
        "fields": [
            {
                "key": "items",
                "label": "رویدادها",
                "type": "itemList",
                "minItems": 1,
                "maxItems": 20,
                "itemFields": [
                    {"key": "date", "label": "تاریخ", "type": "text", "required": True},
                    {"key": "title", "label": "عنوان", "type": "text", "required": True},
                    {"key": "body", "label": "متن", "type": "textarea", "required": False},
                ],
            },
        ],
    },
    {
        "type": "counters",
        "required": ["items"],
        "fields": [
            {
                "key": "items",
                "label": "شمارنده‌ها",
                "type": "itemList",
                "minItems": 1,
                "maxItems": 6,
                "itemFields": [
                    {"key": "value", "label": "مقدار", "type": "text", "required": True},
                    {"key": "label", "label": "برچسب", "type": "text", "required": True},
                ],
            },
        ],
    },
    {
        "type": "before_after",
        "required": ["beforeMediaId", "afterMediaId"],
        "fields": [
            {"key": "beforeMediaId", "label": "تصویر قبل", "type": "media"},
            {"key": "afterMediaId", "label": "تصویر بعد", "type": "media"},
            {"key": "beforeLabel", "label": "برچسب قبل", "type": "text"},
            {"key": "afterLabel", "label": "برچسب بعد", "type": "text"},
            {"key": "beforeCaption", "label": "شرح قبل", "type": "text"},
            {"key": "afterCaption", "label": "شرح بعد", "type": "text"},
        ],
        "mediaFamily": "image",
    },
    {
        "type": "slider",
        "required": ["mediaIds"],
        "fields": [
            {"key": "mediaIds", "label": "تصاویر", "type": "mediaList"},
        ],
        "mediaFamily": "image",
    },
    {
        "type": "divider",
        "required": [],
        "fields": [],
    },
]

BLOCK_FIELD_SPECS: list[dict] = LANDING_BLOCK_FIELD_SPECS

SECTION_LAYOUT_RATIOS: dict[str, list[str]] = {
    "1col": [""],
    "2col": ["1:1", "1:2", "2:1"],
    "3col": ["1:1:1"],
}

SECTION_LAYOUT_LABELS_FA: dict[str, str] = {
    "1col": "۱ ستون",
    "2col": "۲ ستون",
    "3col": "۳ ستون",
}

GALLERY_MAX_MEDIA = 8
SLIDER_MAX_MEDIA = 12
MATH_HTML_MAX_LENGTH = 8000
ITEM_BODY_MAX_LENGTH = 16000


class BlockValidationError(Exception):
    """Raised by ``validate_block_settings`` for an invalid block document."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def allowed_block_types(kind: str) -> list[str]:
    """Block types accepted for a composition kind."""
    if kind == KIND_STORY:
        return list(STORY_BLOCK_TYPES)
    return list(LANDING_BLOCK_TYPES)


def _catalog(kind: str) -> tuple[dict[str, dict], dict[str, dict[str, dict]]]:
    specs = STORY_BLOCK_FIELD_SPECS if kind == KIND_STORY else LANDING_BLOCK_FIELD_SPECS
    spec_by_type = {spec["type"]: spec for spec in specs}
    field_by_type = {
        spec["type"]: {field["key"]: field for field in spec["fields"]}
        for spec in specs
    }
    return spec_by_type, field_by_type


def _check_media_pk(key: str, value, family: str | None = None) -> None:
    """Require ``value`` to be a real Media primary key.

    Only a strict integer is accepted — floats (``1.9``) and booleans
    (``True`` == 1) are rejected so a value cannot silently resolve to an
    existing row. Numeric strings are accepted for editor convenience.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise BlockValidationError(
            f"Setting '{key}' must reference an existing media id."
        )
    if isinstance(value, int):
        pk = value
    elif isinstance(value, str) and value.strip().isdigit():
        pk = int(value)
    else:
        raise BlockValidationError(
            f"Setting '{key}' must reference an existing media id."
        )
    media = Media.objects.filter(pk=pk).only("pk", "mime").first()
    if media is None:
        raise BlockValidationError(
            f"Setting '{key}' must reference an existing media id."
        )
    if family is not None and mime_family(media.mime) != family:
        raise BlockValidationError(
            f"Setting '{key}' must reference {family} media."
        )


def _validate_item_list(key: str, value, field: dict) -> None:
    min_items = int(field.get("minItems", 1))
    max_items = int(field.get("maxItems", 12))
    item_fields: list[dict] = field.get("itemFields") or []
    if not isinstance(value, list):
        raise BlockValidationError(f"Setting '{key}' must be a list.")
    if not (min_items <= len(value) <= max_items):
        raise BlockValidationError(
            f"Setting '{key}' must contain {min_items} to {max_items} items."
        )
    allowed_item_keys = {item_field["key"] for item_field in item_fields}
    required_item_keys = [
        item_field["key"]
        for item_field in item_fields
        if item_field.get("required")
    ]
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise BlockValidationError(
                f"Setting '{key}[{index}]' must be an object."
            )
        extra = sorted(item_key for item_key in item if item_key not in allowed_item_keys)
        if extra:
            raise BlockValidationError(
                f"Unknown item key(s) in '{key}[{index}]': {', '.join(extra)}."
            )
        for item_key in required_item_keys:
            if item_key not in item:
                raise BlockValidationError(
                    f"Missing required item field '{key}[{index}].{item_key}'."
                )
        for item_field in item_fields:
            item_key = item_field["key"]
            if item_key not in item:
                continue
            item_value = item[item_key]
            item_type = item_field["type"]
            if item_type in ("text", "textarea"):
                if not isinstance(item_value, str):
                    raise BlockValidationError(
                        f"Item '{key}[{index}].{item_key}' must be a string."
                    )
                if item_field.get("required") and not item_value.strip():
                    raise BlockValidationError(
                        f"Item '{key}[{index}].{item_key}' must not be empty."
                    )
                if item_type == "textarea" and len(item_value) > ITEM_BODY_MAX_LENGTH:
                    raise BlockValidationError(
                        f"Item '{key}[{index}].{item_key}' must be at most "
                        f"{ITEM_BODY_MAX_LENGTH} characters."
                    )


def validate_block_settings(block_type: str, settings, kind: str = KIND_LANDING) -> None:
    """Fail-closed validation of a block's ``settings`` dict (raises on error)."""
    if kind not in VALID_KINDS:
        raise BlockValidationError(f"Unknown composition kind '{kind}'.")
    spec_by_type, field_by_type = _catalog(kind)
    spec = spec_by_type.get(block_type)
    if spec is None:
        raise BlockValidationError(f"Unknown block type '{block_type}'.")
    if not isinstance(settings, dict):
        raise BlockValidationError("settings must be an object.")

    allowed_keys = set(field_by_type[block_type])
    extra = sorted(key for key in settings if key not in allowed_keys)
    if extra:
        raise BlockValidationError(f"Unknown setting key(s): {', '.join(extra)}.")

    for key in spec["required"]:
        if key not in settings:
            raise BlockValidationError(f"Missing required setting '{key}'.")

    media_fam = spec.get("mediaFamily")
    for key, value in settings.items():
        field = field_by_type[block_type][key]
        ftype = field["type"]
        if ftype in ("text", "textarea"):
            if not isinstance(value, str):
                raise BlockValidationError(f"Setting '{key}' must be a string.")
            if key in spec["required"] and not value.strip():
                raise BlockValidationError(
                    f"Setting '{key}' must not be empty."
                )
            if block_type == "math" and key == "html" and len(value) > MATH_HTML_MAX_LENGTH:
                raise BlockValidationError(
                    f"Setting '{key}' must be at most {MATH_HTML_MAX_LENGTH} characters."
                )
        elif ftype == "number":
            if isinstance(value, bool) or not isinstance(value, int):
                raise BlockValidationError(f"Setting '{key}' must be a number.")
        elif ftype == "select":
            if value not in field["options"]:
                raise BlockValidationError(
                    f"Setting '{key}' must be one of: {', '.join(field['options'])}."
                )
        elif ftype == "media":
            if value is not None:
                _check_media_pk(key, value, family=media_fam)
        elif ftype == "mediaList":
            max_media = SLIDER_MAX_MEDIA if block_type == "slider" else GALLERY_MAX_MEDIA
            if not isinstance(value, list) or not (1 <= len(value) <= max_media):
                raise BlockValidationError(
                    f"Setting '{key}' must be a list of 1 to {max_media} media ids."
                )
            for pk in value:
                _check_media_pk(key, pk, family=media_fam)
        elif ftype == "itemList":
            _validate_item_list(key, value, field)

    if block_type == "divider" and settings:
        raise BlockValidationError("divider block must have empty settings.")


def composition_schema(kind: str = KIND_LANDING) -> dict:
    """Schema metadata sent to the admin SPA (Persian labels)."""
    if kind not in VALID_KINDS:
        kind = KIND_LANDING
    specs = STORY_BLOCK_FIELD_SPECS if kind == KIND_STORY else LANDING_BLOCK_FIELD_SPECS
    return {
        "kind": kind,
        "blockTypes": [
            {
                "type": spec["type"],
                "labelFa": BLOCK_TYPE_LABELS_FA[spec["type"]],
                "required": list(spec["required"]),
                "fields": [dict(field) for field in spec["fields"]],
            }
            for spec in specs
        ],
        "sectionLayouts": [
            {
                "value": value,
                "label": SECTION_LAYOUT_LABELS_FA[value],
                "ratios": list(ratios),
            }
            for value, ratios in SECTION_LAYOUT_RATIOS.items()
        ],
    }
