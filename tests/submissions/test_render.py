"""Tests for the render subcommand's fetch/logo logic (browser capture excluded)."""

from pathlib import Path

import pytest

import process_submission as ps

FIXTURES = Path(__file__).parent / "fixtures"
WAGTAIL_HTML = (FIXTURES / "wagtail-home.html").read_text()


class FakeClient:
    """Records requests; returns canned HTML."""

    def __init__(self, responses: dict[str, tuple[int, str]]):
        self.responses = responses
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        import httpx

        self.requested.append(url)
        status, text = self.responses.get(url, (404, ""))
        if status >= 400:
            raise httpx.HTTPStatusError(f"{status}", request=None, response=None)
        return FakeResponse(status, text, url)


class FakeResponse:
    def __init__(self, status_code, text, url):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": "text/html"}
        self.url = url

    def raise_for_status(self):
        pass


LOCAL_HOSTS = ("example.com", "www.example.com", "final.example", "cdn.example")


def local_resolver(host, port, *args, **kwargs):
    """Deterministic resolver: the test hosts map to a public IP, everything
    else (including real DNS) is rejected. Keeps redirect tests hermetic —
    `final.example` is a reserved TLD that never resolves publicly."""
    if host in LOCAL_HOSTS:
        return [(2, None, None, "", ("93.184.216.34", port))]
    raise OSError(f"cannot resolve {host}")


@pytest.fixture
def url_guard(monkeypatch):
    """Point check_public_url at the deterministic resolver."""
    real = ps.check_public_url
    monkeypatch.setattr(
        ps,
        "check_public_url",
        lambda raw, resolver=None: real(raw, resolver=local_resolver),
    )


class TestFetchPage:
    def test_follows_redirect_chain(self, url_guard):
        hops = ["https://example.com/start", "https://www.example.com/", "https://final.example/"]

        class HopClient(FakeClient):
            def get(self, url, **kwargs):
                self.requested.append(url)
                if url != hops[-1]:
                    resp = FakeResponse(302, "", url)
                    resp.headers = {"location": hops[hops.index(url) + 1], "content-type": "text/html"}
                    return resp
                return FakeResponse(200, WAGTAIL_HTML, url)

        client = HopClient({})
        final_url, html = ps.fetch_page(client, hops[0])
        assert final_url == hops[-1]
        assert "generator" in html
        assert client.requested == hops

    def test_rejects_redirect_to_private_host(self, url_guard):
        # No explicit port: an explicit port trips the default-port guard
        # before the public-IP check, and this test targets the IP check.
        class RedirectClient(FakeClient):
            def get(self, url, **kwargs):
                resp = FakeResponse(302, "", url)
                resp.headers = {"location": "http://127.0.0.1/", "content-type": "text/html"}
                return resp

        with pytest.raises(ValueError, match="public"):
            ps.fetch_page(RedirectClient({}), "https://example.com")

    def test_rejects_too_many_redirects(self, url_guard):
        class LoopClient(FakeClient):
            def get(self, url, **kwargs):
                resp = FakeResponse(302, "", url)
                resp.headers = {"location": "https://example.com/next", "content-type": "text/html"}
                return resp

        with pytest.raises(ValueError, match="redirect"):
            ps.fetch_page(LoopClient({}), "https://example.com")


class TestGatherLogoCandidates:
    def test_explicit_logo_url_first(self, url_guard):
        candidates = ps.gather_logo_candidates(
            FakeClient({}), WAGTAIL_HTML, "https://example.com", "https://cdn.example/logo.png"
        )
        assert candidates[0] == "https://cdn.example/logo.png"

    def test_link_rel_icons(self, url_guard):
        html = '<link rel="apple-touch-icon" href="/touch.png"><link rel="icon" href="/fav.ico">'
        candidates = ps.gather_logo_candidates(FakeClient({}), html, "https://example.com", None)
        assert candidates == [
            "https://example.com/touch.png",
            "https://example.com/fav.ico",
            "https://example.com/apple-touch-icon.png",
            "https://example.com/favicon.ico",
        ]

    def test_private_ip_absolute_link_excluded(self, url_guard):
        # RFC 3986 join: an absolute reference in the page HTML wins over the
        # origin — it must still pass check_public_url before fetching.
        html = '<link rel="icon" href="http://169.254.169.254/latest/meta-data/">'
        candidates = ps.gather_logo_candidates(FakeClient({}), html, "https://example.com", None)
        assert candidates == [
            "https://example.com/apple-touch-icon.png",
            "https://example.com/favicon.ico",
        ]

    def test_private_hostname_link_excluded(self, url_guard):
        # internal.example resolves to a private IP under local_resolver.
        html = '<link rel="icon" href="http://internal.example/icon.png">'
        candidates = ps.gather_logo_candidates(FakeClient({}), html, "https://example.com", None)
        assert candidates == [
            "https://example.com/apple-touch-icon.png",
            "https://example.com/favicon.ico",
        ]


class TestProbeAdminPages:
    def test_redirecting_admin_yields_no_signal(self, url_guard):
        """A redirecting admin page must produce no signal: redirects are only
        followed through fetch_page's per-hop SSRF-checked path."""

        class RedirectClient(FakeClient):
            def get(self, url, **kwargs):
                self.requested.append(url)
                assert "follow_redirects" not in kwargs, "admin probe must not follow redirects"
                resp = FakeResponse(302, "", url)
                resp.headers = {"location": "https://example.com/login", "content-type": "text/html"}
                return resp

        assert ps.probe_admin_pages(RedirectClient({}), "https://example.com") == []

    def test_huge_content_length_skipped(self):
        class HugeClient(FakeClient):
            def get(self, url, **kwargs):
                resp = FakeResponse(200, "wagtail everywhere", url)
                resp.headers = {"content-type": "text/html", "content-length": str(ps.MAX_RESPONSE_BYTES + 1)}
                resp.content = b"x" * 10
                return resp

        assert ps.probe_admin_pages(HugeClient({}), "https://example.com") == []
