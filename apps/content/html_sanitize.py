"""Local HTML allowlist sanitizer for rich-text fields.

Strips XSS vectors (script unwrap, ``javascript:`` href drop, event attrs drop)
and allows a minimal element set including ``blockquote`` / ``code`` so public
projection keeps ADR-0022 editor features. ``img`` / embeds are intentionally
excluded (the editor never allowed them). BeautifulSoup is a direct dependency.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from django.utils.html import escape

ALLOWED_URL_SCHEMES = ["http", "https", "ftp", "mailto", "tel"]
PROTOCOL_RE = re.compile(r"^[a-z0-9][-+.a-z0-9]*:")


def check_url(url_string: str) -> str | None:
    unescaped = url_string.lower()
    unescaped = unescaped.replace("&lt;", "<")
    unescaped = unescaped.replace("&gt;", ">")
    unescaped = unescaped.replace("&amp;", "&")
    unescaped = re.sub(r"[`\000-\040\177-\240\s]+", "", unescaped)
    unescaped = unescaped.replace("\ufffd", "")
    if PROTOCOL_RE.match(unescaped):
        protocol = unescaped.split(":", 1)[0]
        if protocol not in ALLOWED_URL_SCHEMES:
            return None
    return url_string


def attribute_rule(allowed_attrs: dict):
    def fn(tag: Tag) -> None:
        for attr, _val in list(tag.attrs.items()):
            rule = allowed_attrs.get(attr)
            if rule:
                if callable(rule):
                    new_val = rule(tag[attr])
                    if new_val is None:
                        del tag[attr]
                    else:
                        tag[attr] = new_val
            else:
                del tag[attr]

    return fn


allow_without_attributes = attribute_rule({})

# ADR-0022 editor features + structural tags needed for stored rich HTML.
DEFAULT_ELEMENT_RULES = {
    "[document]": allow_without_attributes,
    "a": attribute_rule({"href": check_url}),
    "b": allow_without_attributes,
    "blockquote": allow_without_attributes,
    "br": allow_without_attributes,
    "code": allow_without_attributes,
    "div": allow_without_attributes,
    "em": allow_without_attributes,
    "h1": allow_without_attributes,
    "h2": allow_without_attributes,
    "h3": allow_without_attributes,
    "h4": allow_without_attributes,
    "h5": allow_without_attributes,
    "h6": allow_without_attributes,
    "hr": allow_without_attributes,
    "i": allow_without_attributes,
    "li": allow_without_attributes,
    "ol": allow_without_attributes,
    "p": allow_without_attributes,
    "pre": allow_without_attributes,
    "strong": allow_without_attributes,
    "sub": allow_without_attributes,
    "sup": allow_without_attributes,
    "ul": allow_without_attributes,
}


class HtmlAllowlistSanitizer:
    """Strip disallowed tags/attrs; unwrap unknown tags (keep text children)."""

    element_rules = DEFAULT_ELEMENT_RULES

    def clean(self, html: str) -> str:
        doc = BeautifulSoup(html or "", "html.parser")
        self.clean_node(doc, doc)
        return doc.decode(formatter=escape)

    def clean_node(self, doc, node) -> None:
        if isinstance(node, NavigableString):
            self.clean_string_node(doc, node)
        elif isinstance(node, Tag):
            self.clean_tag_node(doc, node)
        else:  # pragma: no cover
            node.decompose()

    def clean_string_node(self, doc, node) -> None:
        if isinstance(node, Comment):
            node.extract()

    def clean_tag_node(self, doc, tag: Tag) -> None:
        for child in list(tag.contents):
            self.clean_node(doc, child)
        try:
            rule = self.element_rules[tag.name]
        except KeyError:
            tag.unwrap()
            return
        rule(tag)


_SANITIZER = HtmlAllowlistSanitizer()


def sanitize_html(raw: str) -> str:
    """Public entry point used by API projection, preview, and story text."""
    return _SANITIZER.clean(raw or "")
