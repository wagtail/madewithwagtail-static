"""Tests for consent/banner suppression in the browser capture.

These test the constants and the contract that capture_screenshot wires
up — the real Chromium behavior is exercised manually against live sites
(and was against londonmuseum.org.uk, whose Cookiebot dialog motivated
this feature).
"""

import io
import subprocess

from PIL import Image

import process_submission as ps


class TestBannerHideCss:
    """BANNER_HIDE_CSS is a bare selector list interpolated into one rule."""

    def test_is_pure_selector_list(self):
        # The list is substituted into `CSS = \`%s { display: none !important; }\``.
        # Stray braces or backticks would corrupt that rule or the template
        # literal around it.
        assert "{" not in ps.BANNER_HIDE_CSS
        assert "}" not in ps.BANNER_HIDE_CSS
        assert "`" not in ps.BANNER_HIDE_CSS
        assert "${" not in ps.BANNER_HIDE_CSS

    def test_interpolated_into_hidden_rule(self):
        # Regression: the rule wrapper lives in the init script — the list
        # alone (no declaration block) is invalid CSS that Chromium drops.
        assert "{ display: none !important; }" in ps.CONSENT_INIT_JS
        assert ps.BANNER_HIDE_CSS.strip() in ps.CONSENT_INIT_JS

    def test_covers_major_cmp_containers(self):
        for selector in (
            "#onetrust-consent-sdk",
            "#CybotCookiebotDialog",
            "#usercentrics-root",
            "#iubenda-cs-banner",
            "#cmplz-cookiebanner-container",
            "#cky-consent-bar",
            ".cc-window",
            ".cookie-banner",
        ):
            assert selector in ps.BANNER_HIDE_CSS

    def test_generic_cookie_class_fallback(self):
        # Generic attribute-selector fallback, scoped to banner-capable
        # containers (unscoped it would hide recipe grids on food blogs).
        # The i flag covers CamelCase class names. Matches wagtail.org's
        # <div class="cookie"> banner.
        for tag in ("div", "section", "aside", "footer", "header", "dialog"):
            assert f'{tag}[class*="cookie" i]' in ps.BANNER_HIDE_CSS

    def test_no_selector_hides_body_or_html(self):
        # Hiding <body> would blank the whole screenshot. Selectors like
        # ".cookie-consent:not(body):not(html)" carry explicit guards.
        without_comment = ps.BANNER_HIDE_CSS.split("*/", 1)[1]
        selectors = [s.strip() for s in without_comment.split(",") if s.strip()]
        assert selectors  # list is not empty
        for selector in selectors:
            assert selector not in ("body", "html", "*"), selector
            assert not selector.startswith(("body ", "html ", "body>", "html>")), selector


class TestConsentInitJs:
    """The init script must be parseable JS that installs the stylesheet."""

    def test_css_was_interpolated(self):
        # Regression: an unformatted %s produces a JS SyntaxError that
        # kills the whole init script silently.
        assert "%s" not in ps.CONSENT_INIT_JS
        assert "CybotCookiebotDialog" in ps.CONSENT_INIT_JS

    def test_installs_null_safely_and_retries(self):
        # Regression: init scripts run before <head> exists; the first
        # install attempt must bail instead of throwing, and a retry loop
        # must be scheduled for once the document exists.
        assert "if (!root) return;" in ps.CONSENT_INIT_JS
        assert "setInterval" in ps.CONSENT_INIT_JS
        assert "DOMContentLoaded" in ps.CONSENT_INIT_JS

    def test_parses_as_javascript(self, tmp_path):
        # node --check rejects --eval, so parse from a temp module file.
        # Wrapping in a function body defers execution while still parsing.
        script = tmp_path / "init_script.mjs"
        script.write_text(f"(() => {{ {ps.CONSENT_INIT_JS} }})")
        result = subprocess.run(
            ["node", "--check", str(script)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_style_id_is_stable(self):
        assert "style.id = ID;" in ps.CONSENT_INIT_JS
        assert '"__mww_banner_hide"' in ps.CONSENT_INIT_JS


class _FakePlaywright:
    """Records the context/page calls capture_screenshot must make."""

    def __init__(self, png: bytes):
        self.png = png
        self.events = []

    class chromium:
        _instance = None

        @staticmethod
        def launch():
            return _FakePlaywright.chromium._instance

    class _Context:
        def __init__(self, pw):
            self.pw = pw

        def route(self, pattern, guard):
            self.pw.events.append(("route", pattern))

        def add_init_script(self, script):
            self.pw.events.append(("init", script))

        def new_page(self):
            self.pw.events.append(("new_page",))
            return self.pw.page

        def close(self):
            self.pw.events.append(("context_close",))

    class _Page:
        def __init__(self, pw):
            self.pw = pw

        def goto(self, url, **kwargs):
            self.pw.events.append(("goto", url))

        def wait_for_timeout(self, ms):
            self.pw.events.append(("wait", ms))

        def screenshot(self, **kwargs):
            self.pw.events.append(("screenshot", kwargs))
            return self.pw.png

    class _Browser:
        def __init__(self, pw):
            self.pw = pw

        def new_context(self, **kwargs):
            self.pw.events.append(("new_context", kwargs))
            return _FakePlaywright._Context(self.pw)

        def close(self):
            self.pw.events.append(("browser_close",))


class TestCaptureScreenshotContract:
    """capture_screenshot must arm the init script and disable animations."""

    def test_arms_init_script_and_disables_animations(self, monkeypatch, tmp_path):
        png_buf = io.BytesIO()
        Image.new("RGB", (1200, 996), "#123456").save(png_buf, "PNG")

        pw = _FakePlaywright(png_buf.getvalue())
        pw.page = _FakePlaywright._Page(pw)
        _FakePlaywright.chromium._instance = _FakePlaywright._Browser(pw)

        import playwright.sync_api as sync_api

        monkeypatch.setattr(
            sync_api, "sync_playwright", lambda: _nullcontext(pw)
        )

        out = tmp_path / "shot.webp"
        ps.capture_screenshot("https://example.com/", out)

        kinds = [e[0] for e in pw.events]
        assert kinds == [
            "new_context",
            "route",
            "init",
            "new_page",
            "goto",
            "wait",
            "screenshot",
            "context_close",
            "browser_close",
        ]
        init_event = next(e for e in pw.events if e[0] == "init")
        assert init_event[1] == ps.CONSENT_INIT_JS
        screenshot_kwargs = next(e for e in pw.events if e[0] == "screenshot")[1]
        assert screenshot_kwargs.get("animations") == "disabled"
        assert out.exists()  # encode_screenshot ran on the returned PNG


class _nullcontext:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *exc):
        return False
