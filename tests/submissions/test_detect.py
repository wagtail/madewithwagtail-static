from pathlib import Path

import process_submission as ps

FIXTURES = Path(__file__).parent / "fixtures"


class TestDetectWagtail:
    def test_generator_meta(self):
        html = (FIXTURES / "wagtail-home.html").read_text()
        signals = ps.detect_wagtail(html)
        assert any("generator" in s for s in signals)

    def test_static_asset_reference(self):
        html = '<link rel="stylesheet" href="/static/wagtail/css/core.css">'
        assert ps.detect_wagtail(html)

    def test_rendition_url_signal(self):
        # Regression: londonmuseum.org.uk only exposes Wagtail through
        # rendition URLs like WagtailSource-<slug>.<hash>.fill-600x450.format-webp.webp.
        html = '<img src="/media/images/WagtailSource-A.5125cc1f.fill-600x450.format-webp.webp">'
        signals = ps.detect_wagtail(html)
        assert "Wagtail rendition URL in image sources" in signals

    def test_rendition_filter_variants(self):
        for url in (
            "hero.width-496.jpg",
            "img.max-120x120.webp",
            "pic.height-999.png",
            "photo.scale-150.avif",
        ):
            assert ps.WAGTAIL_RENDITION_RE.search(url), url

    def test_non_wagtail_images_not_matched(self):
        # WordPress-style resize names and versioned assets must not trip
        # the rendition fingerprint.
        for url in ("image-600x450.jpg", "jquery.min-3.5.1.js", "photo.jpg"):
            assert not ps.WAGTAIL_RENDITION_RE.search(url), url

    def test_no_signals(self):
        html = (FIXTURES / "plain-home.html").read_text()
        assert ps.detect_wagtail(html) == []

    def test_case_insensitive(self):
        html = '<META NAME="generator" CONTENT="wagtail 5">'
        assert ps.detect_wagtail(html)


class TestDetectionResult:
    def test_shape(self):
        result = ps.detection_result(["generator meta tag"], "https://example.com")
        assert result["is_wagtail"] is True
        assert result["url"] == "https://example.com"
        assert result["signals"] == ["generator meta tag"]
        assert "checked_at" in result

    def test_no_signals_is_false_not_error(self):
        result = ps.detection_result([], "https://example.com")
        assert result["is_wagtail"] is False
