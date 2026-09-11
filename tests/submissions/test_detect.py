from pathlib import Path

import process_submission as ps

FIXTURES = Path(__file__).parent / "fixtures"


class TestDetectWagtail:
    def test_rendition_url_signal(self):
        # Regression: londonmuseum.org.uk only exposes Wagtail through
        # rendition URLs like WagtailSource-<slug>.<hash>.fill-600x450.format-webp.webp.
        html = '<img src="/media/images/WagtailSource-A.5125cc1f.fill-600x450.format-webp.webp">'
        signals = ps.detect_wagtail(html)
        assert "Wagtail rendition URL in image sources (strictest tier)" in signals

    def test_rendition_tiers_reported_most_strict_first(self):
        for url, tier in (
            ("/media/original_images/foo.jpg", "strictest"),
            ("/media/cache/images/foo.fill-600x450.jpg", "strict"),
            ("/images/foo.fill-600x450.jpg", "less_strict_but_long"),
            ("/original_images/foo.jpg", "lax"),
            ("/hero.fill-600x450.jpg", "laxest"),
        ):
            html = f'<img src="{url}">'
            assert ps.rendition_tier(html) == tier, url

    def test_non_wagtail_images_not_matched(self):
        # WordPress-style resize names and versioned assets must not trip
        # the rendition fingerprint.
        for url in ("image-600x450.jpg", "jquery.min-3.5.1.js", "photo.jpg"):
            assert ps.rendition_tier(f'<img src="{url}">') is None, url

    def test_rich_text_block_key(self):
        html = '<p data-block-key="a2x9f">Hello</p>'
        assert "Rich text data-block-key attribute" in ps.detect_wagtail(html)

    def test_responsive_embed_container(self):
        html = '<div class="responsive-object" style="padding-bottom: 56.25%">'
        assert "Responsive embed container (responsive-object)" in ps.detect_wagtail(html)

    def test_responsive_embed_requires_div(self):
        html = '<span class="responsive-object">nope</span>'
        assert ps.detect_wagtail(html) == []

    def test_streamfield_block_classes(self):
        html = '<div class="block w-block-hero"><h1>Hi</h1></div>'
        assert "StreamField block classes (w-block-*)" in ps.detect_wagtail(html)

    def test_streamfield_block_requires_div(self):
        html = '<span class="w-block-hero">nope</span>'
        assert ps.detect_wagtail(html) == []

    def test_no_signals(self):
        html = (FIXTURES / "plain-home.html").read_text()
        assert ps.detect_wagtail(html) == []


class TestDetectionResult:
    def test_shape(self):
        result = ps.detection_result(["generator meta tag"], "https://example.com")
        assert result["is_wagtail"] is True
        assert result["url"] == "https://example.com"
        assert result["signals"] == ["generator meta tag"]
        assert result["technologies"] == {}
        assert "checked_at" in result

    def test_no_signals_is_false_not_error(self):
        result = ps.detection_result([], "https://example.com")
        assert result["is_wagtail"] is False


class TestClassifyTechnologies:
    def test_splits_incompatible_complementary_other(self):
        technologies = {
            "PHP": {"version": "8.2", "categories": ["Programming languages"]},
            "React": {"version": "18", "categories": ["JavaScript frameworks"]},
            "jQuery": {"version": "3.7", "categories": ["JavaScript libraries"]},
        }
        classified = ps.classify_technologies(technologies)
        assert classified["incompatible"] == ["PHP"]
        assert classified["complementary"] == ["React"]
        assert classified["other"] == ["jQuery"]

    def test_non_reportable_categories_dropped(self):
        # Analytics/CDN detections are noise for the technology report.
        technologies = {
            "Google Analytics": {"version": "", "categories": ["Analytics"]},
            "Cloudflare": {"version": "", "categories": ["CDN"]},
        }
        assert ps.classify_technologies(technologies) == {
            "incompatible": [],
            "complementary": [],
            "other": [],
        }

    def test_wagtail_never_reported(self):
        # Wagtail detection stays with our own HTML heuristics.
        technologies = {"Wagtail": {"version": "6", "categories": ["CMS"]}}
        classified = ps.classify_technologies(technologies)
        assert classified == {"incompatible": [], "complementary": [], "other": []}

    def test_no_categories_entry_tolerated(self):
        assert ps.classify_technologies({"Mystery": {"version": "1"}}) == {
            "incompatible": [],
            "complementary": [],
            "other": [],
        }

    def test_empty_input(self):
        assert ps.classify_technologies({}) == {
            "incompatible": [],
            "complementary": [],
            "other": [],
        }
