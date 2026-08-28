"""Local HTML allowlist sanitizer (Wagtail Whitelister replacement)."""

from apps.content.html_sanitize import sanitize_html
from apps.content.models import Article


def test_xss_payload_stripped_by_local_sanitizer():
    cleaned = sanitize_html(
        "<p>hi <script>alert(1)</script>"
        '<img src="https://example.com/x.png" onerror="alert(1)">'
        '<a href="javascript:alert(1)">link</a></p>'
    )
    assert "<script" not in cleaned
    assert "onerror" not in cleaned
    assert 'href="javascript:' not in cleaned
    assert "<img" not in cleaned
    assert cleaned.startswith("<p>")


def test_adr0022_features_preserved():
    cleaned = sanitize_html(
        "<h2>Title</h2><p><b>bold</b> <i>i</i></p>"
        "<ul><li>a</li></ul><blockquote>q</blockquote><hr/><code>c</code>"
        '<p><a href="https://example.com">ok</a></p>'
    )
    assert "<h2>" in cleaned
    assert "<b>" in cleaned
    assert "<blockquote>" in cleaned
    assert "<code>" in cleaned
    assert 'href="https://example.com"' in cleaned


def test_article_body_is_textfield_not_richtext():
    field = Article._meta.get_field("body")
    assert field.get_internal_type() == "TextField"
    assert "wagtail" not in type(field).__module__
