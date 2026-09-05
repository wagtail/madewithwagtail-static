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
