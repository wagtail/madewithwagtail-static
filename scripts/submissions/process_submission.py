#!/usr/bin/env python3
"""Triage showcase site submissions: validate, render, publish.

Subcommands:
    validate --issue-body <file> [--content-dir <dir>]     -> proposal JSON on stdout
    render   --proposal <file> --out-dir <dir> [--url URL] -> detection.json, screenshot.webp, logo.webp
    publish prepare --proposal <file> --detection <file>
              --screenshot <file> [--logo <file>] --repo-root <dir> [--dry-run]
    publish pr      --proposal <file> --detection <file> --repo-root <dir> [--dry-run]

Exit codes: 0 success, 2 rejection (rejection.json written to cwd),
1 unexpected error.
"""

# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.28",
#   "playwright==1.62.0",
#   "pillow>=11",
#   "pydantic>=2.10",
#   "python-slugify>=8",
#   "pyyaml>=6",
# ]
# ///

from __future__ import annotations

import argparse
import difflib
import io
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from slugify import slugify

SLUG_RE = r"^[a-z0-9][a-z0-9-]{0,49}$"

RESERVED_SLUGS = frozenset(
    {
        "index",
        "public",
        "src",
        "dist",
        "images",
        "page",
        "developers",
        "sites",
        "api",
        "admin",
        "assets",
        "static",
    }
)


class Proposal(BaseModel):
    """The single contract between the validate, render, and publish jobs."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    issue_number: int = Field(ge=1)
    submission_type: Literal["existing-developer", "new-developer"]
    site_url: str = Field(min_length=1, max_length=2000)
    site_title: str = Field(min_length=1, max_length=80)
    site_description: str = Field(min_length=1, max_length=500)
    tags: list[str] = Field(max_length=5)
    developer_name: str = Field(min_length=1, max_length=80)
    developer_slug: str = Field(pattern=SLUG_RE)
    site_slug: str = Field(pattern=SLUG_RE)
    developer_exists: bool
    company_url: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=100)
    lat: str | None = Field(default=None)
    lon: str | None = Field(default=None)
    github_user: str | None = Field(default=None, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
    logo_url: str | None = Field(default=None, max_length=2000)
    submitted_at: datetime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FormParseError(ValueError):
    """The issue body is not one of our form submissions."""


CHECKBOX_RE = re.compile(r"^- \[([xX]| )\] (.*)$", re.MULTILINE)

# Only the form's own labels are section boundaries: user-typed Markdown
# containing `### Something` must stay inside the previous field's content.
SECTION_RE = re.compile(
    r"^### (?P<heading>Submission type|Site URL|Site title|Short description|Tags|"
    r"Developer|Company URL|Location|Latitude|Longitude|GitHub username|Logo URL|"
    r"Confirmations)[ \t]*$",
    re.MULTILINE,
)

# The form renders sections in this exact order; it is the yardstick for
# telling real sections from headings forged inside free-text fields.
FORM_HEADINGS = (
    "Submission type",
    "Site URL",
    "Site title",
    "Short description",
    "Tags",
    "Developer",
    "Company URL",
    "Location",
    "Latitude",
    "Longitude",
    "GitHub username",
    "Logo URL",
    "Confirmations",
)
FORM_ORDER = {heading: index for index, heading in enumerate(FORM_HEADINGS)}

# GitHub substitutes this literal for optional fields the submitter left
# blank; it must be treated as unset, not as submitted data.
NO_RESPONSE_PLACEHOLDER = "_No response_"


def parse_issue_form_body(body: str) -> dict[str, str | list[str] | list[tuple[str, bool]]]:
    """Parse a GitHub issue form body into {heading: content}.

    GitHub renders form issues as `### <label>` sections. Multiselect
    values arrive comma-separated; confirmations as a checkbox list.
    Blank optional fields arrive as the `_No response_` placeholder and
    are reported as unset (empty list).
    """
    matches = list(SECTION_RE.finditer(body))
    if not any(match.group("heading") == "Submission type" for match in matches):
        raise FormParseError("Issue body does not look like a site submission form.")

    def parse_section(heading: str, content: str):
        if content == NO_RESPONSE_PLACEHOLDER:
            return []
        if heading == "Confirmations":
            return [
                (label.strip(), mark.casefold() == "x") for mark, label in CHECKBOX_RE.findall(content)
            ]
        if heading == "Tags":
            return [tag.strip() for tag in content.split(",") if tag.strip()]
        return content

    result: dict[str, str | list[str] | list[tuple[str, bool]]] = {}
    # Real sections appear in the form's canonical order; a heading that is
    # out of order or repeats an already-seen section was forged inside a
    # free-text field, so its text stays with the field it was typed in.
    prev_index = -1
    for index, match in enumerate(matches):
        heading = match.group("heading")
        if heading == "Confirmations":
            continue  # handled after the loop: last occurrence always wins
        section_index = FORM_ORDER[heading]
        if section_index <= prev_index or heading in result:
            continue
        prev_index = section_index
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        result[heading] = parse_section(heading, body[match.end() : end].strip())

    # Confirmations is the form's final section, so the last match is the
    # real one even when earlier free text contains a forged heading.
    confirmations = [match for match in matches if match.group("heading") == "Confirmations"]
    if confirmations:
        match = confirmations[-1]
        result["Confirmations"] = parse_section("Confirmations", body[match.end() :].strip())
    return result


# Submissions must not point at our own hosting (self-DoS guard, spec §Security 7).
HOST_BLOCKLIST_SUFFIXES = (
    "github.com",
    "github.io",
    "githubusercontent.com",
)


def _is_browsable_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return False
    return ip.is_global


def check_public_url(raw: str, resolver=socket.getaddrinfo) -> str:
    """Validate a user-supplied URL for browsing; return the normalized URL.

    Enforces the spec's SSRF rules: http(s) only, default port only, no
    userinfo, public DNS resolution, not on the host blocklist.
    Raises pydantic ValidationError with a descriptive message otherwise.
    """
    from pydantic import ValidationError as _VE

    def bad(reason: str) -> Exception:
        # pydantic 2 requires a title; single-dict construction (as the plan
        # sketched) raises TypeError. Build via from_exception_data instead.
        return _VE.from_exception_data(
            "URL validation",
            [
                {
                    "type": "value_error",
                    "loc": ("url",),
                    "input": raw,
                    "ctx": {"error": ValueError(reason)},
                }
            ],
        )

    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise bad(f"URL does not parse: {exc}") from exc

    if parts.scheme not in ("http", "https"):
        raise bad(f"Scheme must be http or https, got {parts.scheme!r}")
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise bad("URL must not contain credentials (userinfo)")
    try:
        explicit_port = parts.port
    except ValueError as exc:
        raise bad("URL has an invalid port") from exc
    if explicit_port is not None:
        raise bad("URL must use the default port")

    hostname = parts.hostname or ""
    if not hostname:
        raise bad("URL has no hostname")

    host_lower = hostname.rstrip(".").lower()
    if any(host_lower == s or host_lower.endswith("." + s) for s in HOST_BLOCKLIST_SUFFIXES):
        raise bad(f"Host {host_lower} is on the submission blocklist")

    # IP literals: validate directly. Hostnames: resolve.
    try:
        ips = [ipaddress.ip_address(host_lower)]
    except ValueError:
        try:
            infos = resolver(host_lower, parts.port or (443 if parts.scheme == "https" else 80))
        except Exception as exc:
            raise bad(f"Hostname {host_lower} does not resolve") from exc
        try:
            ips = [ipaddress.ip_address(info[4][0]) for info in infos]
        except ValueError as exc:
            raise bad(f"Resolver returned a malformed address: {exc}") from exc

    # An empty resolution list must not pass vacuously through all().
    if not ips or not all(_is_browsable_ip(ip) for ip in ips):
        raise bad(f"Host {host_lower} does not resolve to a public-only address")

    # str(SplitResult) returns the repr on Python 3.14+; geturl() returns the
    # URL string on every supported version.
    return parts.geturl()


def read_frontmatter(path: Path) -> dict:
    """Minimal frontmatter reader: YAML between the first two --- lines."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, fm, _ = text.split("---", 2)
    import yaml

    data = yaml.safe_load(fm)
    return data if isinstance(data, dict) else {}


def make_slug(name: str) -> str:
    slug = slugify(name, max_length=50)
    if not re.fullmatch(SLUG_RE, slug):
        raise ValueError(f"Cannot derive a valid slug from {name!r} (got {slug!r})")
    if slug in RESERVED_SLUGS:
        raise ValueError(f"Slug {slug!r} is reserved")
    return slug


def existing_developers(content_dir: Path) -> dict[str, str]:
    devs = {}
    for index in sorted(content_dir.glob("*/index.md")):
        data = read_frontmatter(index)
        title = data.get("title")
        if isinstance(title, str):
            devs[index.parent.name] = title
    return devs


def match_developer(name: str, devs: dict[str, str]) -> tuple[str, bool] | list[str]:
    by_title = {title.casefold(): slug for slug, title in devs.items()}
    hit = by_title.get(name.casefold())
    if hit:
        return hit, True
    close = difflib.get_close_matches(name.casefold(), list(by_title), n=3, cutoff=0.6)
    return sorted(by_title[c] for c in close)


def existing_site_origins(content_dir: Path) -> set[str]:
    origins = set()
    for index in sorted(content_dir.glob("*/*/index.md")):
        url = read_frontmatter(index).get("site_url")
        if isinstance(url, str) and url:
            try:
                parts = urlsplit(url)
                if parts.scheme and parts.hostname:
                    origins.add(f"{parts.scheme}://{parts.hostname.lower()}")
            except ValueError:
                continue
    return origins


def check_slug_free(kind: str, slug: str, content_dir: Path) -> None:
    if slug in RESERVED_SLUGS:
        raise ValueError(f"Slug {slug!r} is reserved")
    if kind == "developer" and (content_dir / slug).exists():
        raise ValueError(f"Developer directory {slug!r} already exists")
    if kind == "site" and any(p.is_dir() for p in content_dir.glob(f"*/{slug}")):
        raise ValueError(f"Site directory {slug!r} already exists for a developer")


SCREENSHOT_MAX_BYTES = 250_000
LOGO_MAX_BYTES = 50_000


def cover_crop(img: Image.Image, target: tuple[int, int]) -> Image.Image:
    """Resize to cover `target`, then center-crop to exactly `target`."""
    tw, th = target
    scale = max(tw / img.width, th / img.height)
    resized = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left = (resized.width - tw) // 2
    top = (resized.height - th) // 2
    return resized.crop((left, top, left + tw, top + th))


def encode_screenshot(
    data: bytes,
    *,
    target: tuple[int, int] = (1200, 996),
    quality: int = 70,
    max_bytes: int = SCREENSHOT_MAX_BYTES,
) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = cover_crop(img, target)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=quality, method=6)
    out = buf.getvalue()
    if len(out) > max_bytes:
        raise ValueError(
            f"Screenshot encodes to {len(out)} bytes, too large (over the {max_bytes} byte cap)"
        )
    return out


def encode_logo(data: bytes, *, max_size: int = 120, max_bytes: int = LOGO_MAX_BYTES) -> bytes:
    img = Image.open(io.BytesIO(data))
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=80, method=6)
    out = buf.getvalue()
    if len(out) > max_bytes:
        raise ValueError(
            f"Logo encodes to {len(out)} bytes, too large (over the {max_bytes} byte cap)"
        )
    return out


def assert_webp(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    if img.format != "WEBP":
        raise ValueError(f"Expected WEBP image, got {img.format}")
    return img


RESPONSIVE_EMBED_RE = re.compile(
    r'<div[^>]*\bclass=["\'][^"\']*\bresponsive-object\b', re.IGNORECASE
)
STREAMFIELD_BLOCK_RE = re.compile(
    r'<div[^>]*\bclass=["\'][^"\']*\bw-block-', re.IGNORECASE
)
RICH_TEXT_RE = re.compile(r'\bdata-block-key=["\'][a-z0-9]{5}["\']')

# Rendition URL tiers, most strict first; detect_wagtail reports only the
# most confident matching tier. Adapted from JS regexes proven against
# real-world Wagtail sites. Character classes use [\w.-] (dash not last):
# Python re rejects a trailing dash inside a range.
WAGTAIL_RENDITION_TIERS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        (
            "strictest",
            r"\/media\/(?:original_images\/[\w-]+\.|images\/[\w.-]+\.((?:fill|max|min)-\d+x\d+(?:-c\d+)?|(?:width|height|scale)-\d+|original)\.)",
        ),
        (
            "strict",
            r"(?:\.[a-z]+|\/media)(?:\/[\w-]+)?\/(?:original_images\/[\w-]+\.|images\/[\w.-]+\.((?:fill|max|min|width|height|scale)-\d|original))",
        ),
        (
            "less_strict_but_long",
            r"(?:\.[a-z]+|\/media)(?:\/[\w-]+)?\/(?:images\/[\w.-]+\.original|original_images\/[\w-]+\.)|\/images\/[\w.-]+\.(?:fill|max|min|width|height|scale)-\d",
        ),
        (
            "lax",
            r"\/(?:original_images\/[\w-]+\.|images\/[\w.-]+\.((?:fill|max|min|width|height|scale)-\d|original))",
        ),
        (
            "laxest",
            r"\/original_images\/|\/[\w.-]+\.((?:fill|max|min|width|height|scale)-\d|original)",
        ),
    )
)


ADMIN_PATHS = ("/admin/", "/cms/", "/cms-admin/")


def rendition_tier(html: str) -> str | None:
    """Most strict rendition tier matching anywhere in the page, if any."""
    for name, pattern in WAGTAIL_RENDITION_TIERS:
        if pattern.search(html):
            return name
    return None


def detect_wagtail(html: str) -> list[str]:
    """Best-effort Wagtail fingerprints from page HTML (spec: evidence, never a gate)."""
    signals = []
    tier = rendition_tier(html)
    if tier is not None:
        signals.append(f"Wagtail rendition URL in image sources ({tier} tier)")
    if RICH_TEXT_RE.search(html):
        signals.append("Rich text data-block-key attribute")
    if RESPONSIVE_EMBED_RE.search(html):
        signals.append("Responsive embed container (responsive-object)")
    if STREAMFIELD_BLOCK_RE.search(html):
        signals.append("StreamField block classes (w-block-*)")
    return signals



def _response_too_large(response) -> bool:
    """Best-effort size gate: honor content-length when present."""
    headers = getattr(response, "headers", None) or {}
    content_length = headers.get("content-length")
    return bool(content_length and content_length.isdigit() and int(content_length) > MAX_RESPONSE_BYTES)


def _capped_text(response) -> str:
    """Decode at most MAX_RESPONSE_BYTES of the response body."""
    if _response_too_large(response):
        return ""
    content = getattr(response, "content", None)
    if content is None:
        return response.text
    return content[:MAX_RESPONSE_BYTES].decode("utf-8", errors="replace")


def probe_admin_pages(client: "httpx.Client", origin: str) -> list[str]:
    """Best-effort admin probe; every failure mode is silently skipped.

    No redirect following: fetch_page's per-hop SSRF checks are the only
    validated network path, so a redirecting admin simply yields no signal.
    """
    import httpx

    signals = []
    for path in ADMIN_PATHS:
        try:
            response = client.get(origin + path, timeout=5)
        except httpx.HTTPError:
            continue
        body = _capped_text(response)
        if response.status_code == 200 and "wagtail" in body.casefold():
            signals.append(f"Wagtail admin page at {path}")
    return signals


def detection_result(signals: list[str], url: str) -> dict:
    return {
        "url": url,
        "is_wagtail": bool(signals),
        "signals": signals,
        "checked_at": utcnow().isoformat(),
    }


MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 3_000_000
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def fetch_page(client: "httpx.Client", url: str) -> tuple[str, str]:
    """GET a page following redirects manually, SSRF-checking every hop.

    Status-code redirect detection and getattr fallbacks keep this testable
    against minimal fake responses (no httpx.Response required).
    """
    import httpx

    current = check_public_url(url)
    for _ in range(MAX_REDIRECTS):
        response = client.get(
            current, timeout=15, headers={"user-agent": "madewithwagtail-submission-bot"}
        )
        if response.status_code in REDIRECT_STATUSES:
            location = response.headers.get("location", "")
            if not location:
                raise ValueError("Redirect without a location header")
            current = check_public_url(str(httpx.URL(str(response.url)).join(location)))
            continue
        # Cap the body at MAX_RESPONSE_BYTES before decoding.
        content = getattr(response, "content", None)
        if content is None:
            content = response.text.encode("utf-8", errors="replace")
        encoding = getattr(response, "encoding", None) or "utf-8"
        text = content[:MAX_RESPONSE_BYTES].decode(encoding, errors="replace")
        return str(response.url), text
    raise ValueError(f"More than {MAX_REDIRECTS} redirects")


ICON_REL_RE = re.compile(
    r"<link[^>]+rel=[\"'][^\"']*(?:apple-touch-icon|icon)[^\"']*[\"'][^>]*>", re.IGNORECASE
)
ICON_HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)
MANIFEST_HREF_RE = re.compile(
    r"<link[^>]+rel=[\"'][^\"']*manifest[^\"']*[\"'][^>]*>", re.IGNORECASE
)

def gather_logo_candidates(
    client: "httpx.Client",
    html: str,
    origin: str,
    logo_url: str | None,
    resolver=socket.getaddrinfo,
) -> list[str]:
    """Candidate logo URLs: explicit submission first, then <link> icons,
    then web app manifest entries, then conventional paths. All normalized
    against the origin and run through the same SSRF checks as page fetches
    — candidates from attacker-controlled HTML must never bypass
    check_public_url."""
    import httpx

    candidates: list[str] = []

    def add(raw: str) -> None:
        try:
            absolute = str(httpx.URL(origin).join(raw))
        except ValueError:
            return
        try:
            validated = check_public_url(absolute, resolver=resolver)
        except Exception:
            return  # best-effort: invalid/private candidates are simply skipped
        if validated not in candidates:
            candidates.append(validated)

    if logo_url:
        add(logo_url)
    for tag in ICON_REL_RE.findall(html):
        match = ICON_HREF_RE.search(tag)
        if match:
            add(match.group(1))
    # Web app manifest: many sites declare only small favicon links but
    # list large icons (commonly 512x512) in their manifest. Best-effort:
    # an unreadable or non-JSON manifest is skipped.
    for tag in MANIFEST_HREF_RE.findall(html):
        match = ICON_HREF_RE.search(tag)
        if not match:
            continue
        try:
            manifest_url = check_public_url(
                str(httpx.URL(origin).join(match.group(1))), resolver=resolver
            )
            manifest = json.loads(client.get(manifest_url, timeout=5).text)
            icons = manifest.get("icons")
            if not isinstance(icons, list):
                continue
        except Exception:
            continue  # best-effort: unreachable or malformed manifest

        def manifest_entry_size(entry: object) -> int:
            """Largest dimension of a manifest icon's declared sizes.

            W3C format is "sizes": "512x512"; also accepts an object with
            width/height fields. Multiple sizes rank by the largest.
            """
            if not isinstance(entry, dict):
                return 0
            sizes = entry.get("sizes")
            declared: list[int] = []
            if isinstance(sizes, str):
                for token in sizes.split():
                    dims = token.lower().split("x")
                    if len(dims) == 2 and dims[0].isdigit() and dims[1].isdigit():
                        declared.extend((int(dims[0]), int(dims[1])))
            elif isinstance(sizes, (list, dict)):
                values = sizes if isinstance(sizes, list) else sizes.values()
                for value in values:
                    if isinstance(value, (int, float)):
                        declared.append(int(value))
            return max(declared, default=0)

        sized = sorted(
            enumerate(icons),
            key=lambda pair: manifest_entry_size(pair[1]),
            reverse=True,
        )
        for _, entry in sized:
            if isinstance(entry, dict) and isinstance(entry.get("src"), str):
                add(entry["src"])
    add("/apple-touch-icon.png")
    add("/favicon.ico")
    return candidates


def select_largest_logo(
    client: "httpx.Client", candidates: list[str]
) -> bytes | None:
    """Download logo candidates and return the largest decodable image.

    Pages declare icons in arbitrary order (a 16px <link rel="icon">
    commonly precedes the 180px apple-touch-icon), and sizes attributes are
    unreliable across implementations, so every candidate is measured after
    download. Ties keep the earlier candidate — the docstring ordering of
    gather_logo_candidates is most-authoritative-first. Any candidate that
    errors, is oversized, or fails to decode is skipped.
    """
    import httpx

    measured: list[tuple[int, int, bytes]] = []
    for index, candidate in enumerate(candidates):
        try:
            response = client.get(candidate, timeout=10)
            if response.status_code != 200 or _response_too_large(response):
                continue
            # No magic-byte sniffing (it misses ICO favicons): try to decode
            # + encode with Pillow; any failure means "not a usable logo".
            # UnidentifiedImageError is an OSError.
            data = response.content[:MAX_RESPONSE_BYTES]
            width, height = Image.open(io.BytesIO(data)).size
            measured.append((width * height, -index, encode_logo(data)))
        except (httpx.HTTPError, ValueError, OSError):
            continue
    if not measured:
        return None
    return max(measured)[2]


def is_private_browser_host(url: str) -> bool:
    """True if the URL points at a private/loopback host literal.

    Used by the Playwright route guard. Hostnames cannot be resolved
    here, so only IP literals and 'localhost' are classified;
    hostname-based SSRF is backstopped by the credential-free container.
    """
    host = (urlsplit(url).hostname or "").rstrip(".")
    if not host:
        return False
    try:
        return not _is_browsable_ip(ipaddress.ip_address(host))
    except ValueError:
        return host.casefold() == "localhost"



# Consent/banner suppression for screenshots. The selector list mirrors the
# container IDs used by the major CMP SDKs plus common home-grown banners;
# grounded in AdGuard's maintained "Cookie Notices" filter.
BANNER_HIDE_CSS = """\
/* Major CMP SDK containers (grounded in AdGuard's Cookie Notices filter) */
#onetrust-consent-sdk, #onetrust-banner-sdk, #onetrust-pc-sdk,
#optanon-popup-bg, .optanon-show-settings,
#CybotCookiebotDialog, #CybotCookiebotDialogBodyUnderlay, .CybotCookiebotDialogBodyOverlay,
#usercentrics-root, #usercentrics-cmp-ui, #fc-consent-root,
#didomi-host, #didomi-popup, .didomi-host,
#sp_message_container, .sp-message-container, #sp_privacy_manager_container,
#qc-cmp2-container, #qc-cmp2-ui, .qc-cmp2-summary-buttons,
#truste-consent-track, .truste-consent-track, #truste-consent-content,
#Osano-CookieDialog, .osano-cm-window, .osano-cm-info,
#termly-code-snippet-support, .termly-consent-banner,
#iubenda-cs-banner, .iubenda-cs-banner,
#cmplz-cookiebanner-container, .cmplz-cookiebanner,
#cookie-script, .cookiescript_injected_wrapper, #cookiescript_injected,
#klaro, .klaro, .cookie-consent:not(body):not(html),
#klaro0, .klaro-manager-overlay,
#tarteaucitronRoot, .tarteaucitron-root, #tarteaucitronAlertBig, .tarteaucitron-banner,
#BorlabsCookieBox, .borlabs-hide,
#ccm-widget, #ccm-block,
#cky-consent-bar, .cky-consent-bar, .cky-consent-container, #cky-overlay, .cky-overlay,
#cm, #cc-banner, .cc-window, .cc-banner, .cc-revoke,
#cookiebanner, .cookie-banner, #cookie-banner, #cookie_consent, .cookie_consent,
#cookie-law-info-bar, #cookie-law-info-again, .cli-bar-container, #cliSettingsPopup,
.cli-popupbar-overlay, .cli-modal-backdrop,
#moove_gdpr_cookie_info_bar, #moove_gdpr_cookie_modal,
#gdpr-cookie-message, .gdpr-cookie-notice, .gdpr-banner, #gdpr-banner, .gdpr_cookie_bar,
.eu-cookie-compliance-banner:not(body):not(html), .eu-cookie-compliance-overlay,
.js-cookie-banner, .js-cookie-consent, .cookie-notice:not(body):not(html),
.cookie-consent-banner, .cookie-alert:not(body):not(html), .cookie-bar:not(body):not(html),
.cookie-warning, .cookie-policy:not(body):not(html), #cookie-policy,
.cookie-popup, .cookie-popup-wrapper, .cookies-wrapper,
#cookie-box, .cookie-box:not(body):not(html), #cookie-bar, .cookie-bar-overlay,
#cookie-hint, #cookiehint, #cookie-hinweis, .cookie-hint,
#cookies-banner, #cookies-banner-container, .cookies-banner,
#cookie-msg, #cookie-message, .cookie-message:not(body):not(html),
.cookies-eu-banner, #cookie-law-banner, .cookie-law-banner,
#cookiesck, .sqs-cookie-banner-v2, .wpgdprc-consent-bar,
.avia-cookie-consent-wrap, .fusion-privacy-bar, .woodmart-cookies-popup,
.thb-cookie-bar, .pum-open .pum-overlay, .elementor-popup-modal:not(:empty),
/* Generic fallback: any container whose class mentions "cookie". Scoped to
   banner-capable container elements — an unscoped [class*="cookie"] would
   hide recipe content on food blogs (e.g. .cookie-recipes-grid) and blank
   the screenshot. The i flag covers CamelCase classes. */
div[class*="cookie" i], section[class*="cookie" i], aside[class*="cookie" i],
footer[class*="cookie" i], header[class*="cookie" i], dialog[class*="cookie" i],
/* Same scoping for id-based banners: id attribute mentioning "cookie". */
div[id*="cookie" i], section[id*="cookie" i], aside[id*="cookie" i],
footer[id*="cookie" i], header[id*="cookie" i], dialog[id*="cookie" i]
"""
CONSENT_INIT_JS = """\
(() => {
  const CSS = `%s { display: none !important; }`;
  const ID = "__mww_banner_hide";
  const install = () => {
    if (document.getElementById(ID)) return;
    // Init scripts run before parsing starts: <head> and <html> are both
    // null then. Bail quietly; the interval below retries until it exists.
    const root = document.head || document.documentElement;
    if (!root) return;
    const style = document.createElement("style");
    style.id = ID;
    style.textContent = CSS;
    root.appendChild(style);
  };
  document.addEventListener("DOMContentLoaded", install);
  window.addEventListener("load", install);
  // Re-assert for 12s: keeps the hide-style last in the cascade (CMPs that
  // inject their own !important rules later lose the specificity battle)
  // and re-installs if a CMP script strips foreign styles from <head>.
  const interval = setInterval(install, 500);
  setTimeout(() => clearInterval(interval), 12_000);
  // Some banners release scroll only via their own handlers; pressing
  // Escape dismisses dialogs that survive CSS hiding.
  const escape = () => { try { window.top.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", keyCode: 27})); } catch (e) {} };
  document.addEventListener("DOMContentLoaded", escape);
  window.addEventListener("load", escape);
  setTimeout(escape, 2000);
})();
""" % BANNER_HIDE_CSS


def capture_screenshot(url: str, out_path: Path) -> None:
    """Load the URL in headless Chromium and save an encoded WebP screenshot.

    Consent banners are suppressed in two layers: an init script installs a
    hide-style before site scripts run (so banner markup often never mounts),
    and a post-load re-application keeps the style last in the cascade —
    CMPs that inject their own !important rules later lose the specificity
    battle. Escapes are re-armed afterwards to release scroll locks and
    backdrops; hiding alone can leave those behind.
    """
    from playwright.sync_api import sync_playwright

    def route_guard(route):
        if is_private_browser_host(route.request.url):
            route.abort()
        else:
            route.continue_()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1200, "height": 996}, device_scale_factor=1
        )
        context.route("**/*", route_guard)
        context.add_init_script(CONSENT_INIT_JS)
        page = context.new_page()
        try:
            page.goto(url, timeout=30_000, wait_until="load")
            page.wait_for_timeout(2000)
            png = page.screenshot(type="png", animations="disabled")
        finally:
            context.close()
            browser.close()
    out_path.write_bytes(encode_screenshot(png))


def cmd_render(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="render")
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    # --url smoke mode (local browser testing) skips the proposal entirely.
    proposal: Proposal | None = None
    if args.proposal:
        proposal = Proposal.model_validate_json(args.proposal.read_text(encoding="utf-8"))
    elif not args.url:
        print("render: either --proposal or --url is required", file=sys.stderr)
        return ERROR_EXIT
    target_url = args.url or proposal.site_url

    import httpx

    args.out_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client() as client:
        final_url, html = fetch_page(client, target_url)
        signals = detect_wagtail(html)
        final_parts = urlsplit(final_url)
        origin = f"{final_parts.scheme}://{final_parts.hostname}"
        signals += probe_admin_pages(client, origin)

        logo_bytes: bytes | None = None
        if proposal is not None and proposal.submission_type == "new-developer":
            logo_bytes = select_largest_logo(
                client,
                gather_logo_candidates(client, html, origin, proposal.logo_url),
            )
    detection = detection_result(signals, final_url)
    (args.out_dir / "detection.json").write_text(
        json.dumps(detection, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    capture_screenshot(final_url, args.out_dir / "screenshot.webp")
    if logo_bytes:
        (args.out_dir / "logo.webp").write_bytes(logo_bytes)
    return 0


REJECTION_EXIT = 2
ERROR_EXIT = 1

CONFIRMATION_LABELS = (
    "I am affiliated with this site or have permission to submit it",
    "This is a production website built with Wagtail",
)

SUBMISSION_TYPES = {
    "A new site and new developer profile": "new-developer",
    "A new site on an existing profile": "existing-developer",
}

LAT_RE = re.compile(r"^-?(?:[0-8]?\d|90)(?:\.\d+)?$")
LON_RE = re.compile(r"^-?(?:\d{1,2}|1[0-7]\d|180)(?:\.\d+)?$")


class Rejection(Exception):
    def __init__(self, *reasons: str):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def build_proposal(
    body: str,
    issue_number: int,
    content_dir: Path,
    resolver=socket.getaddrinfo,
    now: datetime | None = None,
) -> Proposal:
    reasons: list[str] = []
    try:
        fields = parse_issue_form_body(body)
    except FormParseError:
        raise Rejection("This issue was not created with the site submission form.")

    def field(label: str) -> str:
        value = fields.get(label, "")
        return value.strip() if isinstance(value, str) else ""

    # Submission type.
    submission_type = SUBMISSION_TYPES.get(field("Submission type"))
    if submission_type is None:
        reasons.append("Choose one of the two submission types at the top of the form.")
        submission_type = "existing-developer"

    # Confirmations.
    confirmations = fields.get("Confirmations", [])
    for label in CONFIRMATION_LABELS:
        checked = any(label in item_label and ok for item_label, ok in confirmations)
        if not checked:
            reasons.append(f"Tick the confirmation: “{label}.”")

    # URL.
    site_url = ""
    raw_url = field("Site URL")
    if not raw_url:
        reasons.append("Fill in the site URL.")
    else:
        try:
            site_url = check_public_url(raw_url, resolver=resolver)
        except Exception as exc:
            reasons.append(f"The site URL was rejected: {_validation_message(exc)}")

    # Required text fields.
    site_title = field("Site title")
    if not site_title:
        reasons.append("Fill in the site title.")
    site_description = field("Short description")
    if not site_description:
        reasons.append("Fill in the short description.")
    developer_name = field("Developer")
    if not developer_name:
        reasons.append("Fill in the developer name.")

    # Tags (already list-valued from the parser).
    tags = [t for t in (fields.get("Tags") or []) if isinstance(t, str) and t][:5]

    # Developer existence / slug.
    developer_exists = submission_type == "existing-developer"
    developer_slug = ""
    if developer_name:
        if developer_exists:
            devs = existing_developers(content_dir)
            result = match_developer(developer_name, devs)
            if isinstance(result, list):
                hint = f" Existing developers with similar names: {', '.join(result)}." if result else ""
                reasons.append(
                    f"No developer named {developer_name!r} is listed yet.{hint} "
                    "Pick the exact name, or submit as a new developer profile."
                )
            else:
                developer_slug = result[0]
        else:
            try:
                developer_slug = make_slug(developer_name)
                check_slug_free("developer", developer_slug, content_dir)
            except ValueError as exc:
                reasons.append(f"The developer name is not usable: {exc}")

    # Site slug + dedup.
    site_slug = ""
    if site_title:
        try:
            site_slug = make_slug(site_title)
            check_slug_free("site", site_slug, content_dir)
        except ValueError as exc:
            reasons.append(f"The site title is not usable as a page name: {exc}")

    if site_url:
        origin = urlsplit(site_url)
        origin_key = f"{origin.scheme}://{origin.hostname.lower()}" if origin.hostname else ""
        if origin_key and origin_key in existing_site_origins(content_dir):
            reasons.append(f"{origin_key} is already in the showcase.")

    # Optional fields.
    company_url = ""
    if field("Company URL"):
        try:
            company_url = check_public_url(field("Company URL"), resolver=resolver)
        except Exception as exc:
            reasons.append(f"The company URL was rejected: {_validation_message(exc)}")

    logo_url = ""
    if field("Logo URL"):
        try:
            logo_url = check_public_url(field("Logo URL"), resolver=resolver)
        except Exception as exc:
            reasons.append(f"The logo URL was rejected: {_validation_message(exc)}")

    location = field("Location") or None
    lat = field("Latitude") or None
    lon = field("Longitude") or None
    if lat and not LAT_RE.fullmatch(lat):
        reasons.append("Latitude must be a decimal degrees value between -90 and 90.")
        lat = None
    if lon and not LON_RE.fullmatch(lon):
        reasons.append("Longitude must be a decimal degrees value between -180 and 180.")
        lon = None

    github_user = field("GitHub username") or None

    if reasons:
        raise Rejection(*reasons)

    try:
        return Proposal(
            schema_version=1,
            issue_number=issue_number,
            submission_type=submission_type,
            site_url=site_url,
            site_title=site_title,
            site_description=site_description,
            tags=tags,
            developer_name=developer_name,
            developer_slug=developer_slug,
            site_slug=site_slug,
            developer_exists=developer_exists,
            company_url=company_url or None,
            location=location,
            lat=lat,
            lon=lon,
            github_user=github_user,
            logo_url=logo_url or None,
            submitted_at=now or utcnow(),
        )
    except ValidationError as exc:
        # Model-level caps (title > 80, description > 500, ...) are reachable
        # through the real form — they must surface as a structured rejection,
        # not an exit-1 traceback that silently drops the submission.
        raise Rejection(*(_proposal_error_reason(error) for error in exc.errors())) from exc


# Model-level constraints on user-editable fields, mapped to rejection copy.
PROPOSAL_ERROR_REASONS = {
    "site_title": "The site title must be at most 80 characters.",
    "site_description": "The short description must be at most 500 characters.",
    "developer_name": "The developer name must be at most 80 characters.",
    "tags": "Choose at most 5 tags.",
    "location": "The location must be at most 100 characters.",
}


def _proposal_error_reason(error: dict) -> str:
    loc = error.get("loc") or ()
    field = str(loc[-1]) if loc else ""
    return PROPOSAL_ERROR_REASONS.get(
        field,
        f"An entry in the form was rejected: {error.get('msg', 'invalid value')}.",
    )


def _validation_message(exc: Exception) -> str:
    """Human-readable first message from a pydantic ValidationError."""
    if hasattr(exc, "errors") and callable(exc.errors):
        errors = exc.errors()
        if errors:
            return str(errors[0].get("msg", exc))
    return str(exc)


def cmd_validate(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="validate")
    parser.add_argument("--issue-body", required=True, type=Path)
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--content-dir", type=Path, default=Path("src/content/developers"))
    args = parser.parse_args(argv)
    body = args.issue_body.read_text(encoding="utf-8")
    try:
        proposal = build_proposal(body, args.issue_number, args.content_dir)
    except Rejection as rejection:
        print(json.dumps({"reasons": rejection.reasons}, indent=2, ensure_ascii=False))
        return REJECTION_EXIT
    print(proposal.model_dump_json(indent=2))
    return 0


def _frontmatter_block(data: dict) -> str:
    import yaml

    # Optional fields left unset are omitted rather than written as null:
    # the Astro schemas default them, and `key: null` in committed content
    # is noise reviewers shouldn't see.
    data = {key: value for key, value in data.items() if value is not None}
    return "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True) + "---\n"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def site_markdown(p: Proposal) -> str:
    frontmatter = {
        "title": p.site_title,
        "first_published_at": _iso(p.submitted_at),
        "latest_revision_created_at": _iso(p.submitted_at),
        "site_url": p.site_url,
        "in_cooperation_with_slug": None,
        "tags": p.tags,
    }
    return _frontmatter_block(frontmatter) + f"\n{p.site_description}\n"


def developer_markdown(p: Proposal) -> str:
    frontmatter = {
        "title": p.developer_name,
        "first_published_at": _iso(p.submitted_at),
        "latest_revision_created_at": _iso(p.submitted_at),
        "location": p.location,
        "lat": p.lat,
        "lon": p.lon,
        "company_url": p.company_url,
        "twitter_handler": None,
        "github_user": p.github_user,
        "online_profiles": [],
    }
    return _frontmatter_block(frontmatter)


def output_paths(p: Proposal) -> dict[str, Path]:
    paths = {
        "site_md": Path(f"src/content/developers/{p.developer_slug}/{p.site_slug}/index.md"),
        "screenshot": Path(f"public/images/{p.developer_slug}/{p.site_slug}.fill-1200x996.webp"),
    }
    if not p.developer_exists:
        paths["developer_md"] = Path(f"src/content/developers/{p.developer_slug}/index.md")
        paths["logo"] = Path(f"public/images/{p.developer_slug}.max-120x120.webp")
    return paths


def _file_line_count(path: Path) -> int:
    """Number of lines in a committed file, for deep-link ranges."""
    return len(path.read_text(encoding="utf-8").splitlines())


PR_LABEL = "🤖 new site submission"
NEEDS_TRIAGE_LABEL = "needs-triage"
PR_CREATED_LABEL = "submission → PR created"
LIVE_SITE_URL = "https://madewithwagtail.org"


def _profile_line(p: Proposal) -> str:
    """Developer table cell: name linked to their website, profile link after.

    The name links to the developer's own site so the row works for both
    new and existing profiles; the profile-page link only exists once the
    profile is live.
    """
    if p.company_url:
        name = f"[{p.developer_name}]({p.company_url})"
    elif p.developer_exists:
        name = f"[{p.developer_name}]({LIVE_SITE_URL}/developers/{p.developer_slug}/)"
    else:
        name = p.developer_name
    if p.developer_exists:
        suffix = f" - [see profile page]({LIVE_SITE_URL}/developers/{p.developer_slug}/)"
    else:
        suffix = " - new 🎉"
    return name + suffix


def _tag_links(p: Proposal) -> str:
    """Tags linking to the live site's tag pages, as the site renders them."""
    if not p.tags:
        return "_(none)_"
    return ", ".join(
        f"[{tag}]({LIVE_SITE_URL}/sites/tag/{tag.lower()}/)" for tag in p.tags
    )


def _run_footer(run_url: str) -> str:
    """Small-print footer shared by the PR body and issue comments."""
    return (
        f"<sub>View the [site submission workflow logs]({run_url}).</sub>"
    )



def _detection_value(detection: dict) -> str:
    """Concise table-cell status: verdict plus the signals behind it."""
    if detection["is_wagtail"]:
        signals = "; ".join(detection["signals"]) or "Wagtail detected"
        return f"✅ {signals}"
    return "⚠️ No Wagtail signals detected"


def _committed_file_url(
    path: Path,
    repo_full_name: str,
    head_sha: str | None,
    line_count: int | None,
) -> str:
    """Raw deep link to a committed file, rendered as an inline file viewer.

    The L1-L<last> range makes GitHub render the file directly in the PR
    description; it needs the branch's HEAD SHA and the file's line count.
    The URL is emitted bare — reviewers asked for raw links, not markdown
    links — so GitHub auto-links the visible URL itself.
    """
    if head_sha is None:
        return "(SHA unavailable in dry-run)"
    last = line_count if line_count is not None else 1
    return f"https://github.com/{repo_full_name}/blob/{head_sha}/{path}?plain=1#L1-L{last}"


def build_pr_body(
    p: Proposal,
    detection: dict,
    repo_full_name: str,
    branch: str,
    run_url: str,
    logo_committed: bool | None = None,
    head_sha: str | None = None,
    entry_line_count: int | None = None,
    profile_line_count: int | None = None,
) -> str:
    paths = output_paths(p)
    # Whether the logo image was actually written to the branch. None keeps
    # the default "the proposal expects one" for callers that don't know.
    logo_expected = logo_committed if logo_committed is not None else "logo" in paths
    # 300x249 is the 1200x996 capture scaled down; GitHub renders raw
    # width/height img attributes inside PR descriptions.
    screenshot_cell = (
        f'<img src="https://raw.githubusercontent.com/{repo_full_name}/{branch}/{paths["screenshot"]}"'
        ' width="300" height="249" alt="Screenshot of the new site">'
    )
    lines = [
        f"Closes #{p.issue_number}. Auto-generated PR via the [site submission workflow]"
        "(https://github.com/wagtail/madewithwagtail-static/blob/main/CONTRIBUTING.md#site-submissions)"
        f" ([view logs]({run_url})).",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Site | <{p.site_url}> |",
        f"| Developer | {_profile_line(p)} |",
        f"| Tags | {_tag_links(p)} |",
        f"| Detection | {_detection_value(detection)} |",
        f"| Screenshot | {screenshot_cell} |",
    ]
    if not p.developer_exists and logo_expected:
        logo_cell = (
            f'<img src="https://raw.githubusercontent.com/{repo_full_name}/{branch}/{paths["logo"]}"'
            ' width="120" alt="Logo of the developer">'
        )
        lines.append(f"| Logo | {logo_cell} |")
    lines += [
        f"| Local preview | `/developers/{p.developer_slug}/{p.site_slug}` |",
        "",
        "### Site page",
        "",
        _committed_file_url(
            paths["site_md"], repo_full_name, head_sha, entry_line_count
        ),
        "",
    ]
    if not p.developer_exists and "developer_md" in paths:
        lines += [
            "### Developer profile page",
            "",
            _committed_file_url(
                paths["developer_md"], repo_full_name, head_sha, profile_line_count
            ),
            "",
        ]
    lines += [
        "### Reviewer checklist",
        "",
        "- [ ] Site is live and built with Wagtail",
        "- [ ] Screenshot shows the site (not a cookie banner or login page)",
        "- [ ] Tags are sensible",
        "- [ ] Description reads well",
        "- [ ] Developer details are correct" + (" (new profile: check the logo)" if not p.developer_exists else ""),
    ]
    return "\n".join(lines)


def git_add_paths(p: Proposal, repo_root: Path) -> list[Path]:
    """Content paths that exist on disk — a missing logo is legitimate, and
    `git add` on a pathspec that matches nothing fails the publish stage."""
    return [
        path
        for rel in output_paths(p).values()
        if (repo_root / rel).exists()
        for path in [repo_root / rel]
    ]


def build_pr_comment(p: Proposal, pr_url: str, run_url: str) -> str:
    return (
        f"Opened pull request {pr_url} with this submission. "
        f"The issue auto-closes when the PR is merged.\n\n"
        f"Workflow run (artifacts): {run_url}\n"
        + _run_footer(run_url)
    )


def build_rejection_comment(reasons: list[str], run_url: str) -> str:
    bullets = "\n".join(f"- {reason}" for reason in reasons)
    return (
        "Thanks for your submission! Unfortunately it could not be processed:\n\n"
        f"{bullets}\n\n"
        "Feel free to open a new submission once these points are addressed.\n"
        + _run_footer(run_url)
    )


def build_failure_comment(stage: str, error: str, run_url: str) -> str:
    return (
        f"The submission pipeline failed at the **{stage}** stage: {error}\n\n"
        f"Check the [workflow run]({run_url}) for details — a maintainer will follow up "
        f"(labelled {NEEDS_TRIAGE_LABEL}).\n"
        + _run_footer(run_url)
    )


BRANCH_PREFIX = "submission/issue-"


def write_content_files(
    p: Proposal, repo_root: Path, screenshot: bytes, logo: bytes | None
) -> list[Path]:
    """Write validated content into the repo. Images are re-encoded through
    Pillow and dimension-checked — artifact bytes are never trusted as-is."""
    paths = output_paths(p)

    screenshot_img = assert_webp(screenshot)
    if screenshot_img.size != (1200, 996):
        raise ValueError(f"Screenshot must be 1200x996, got {screenshot_img.size}")
    reencoded_screenshot = encode_screenshot(screenshot)

    logo_out: bytes | None = None
    if not p.developer_exists:
        if logo is None:
            raise ValueError("New developer submission requires a logo (may be empty)")
        if logo:
            assert_webp(logo)  # format check; encode_logo normalizes the size
            logo_out = encode_logo(logo)
            if max(Image.open(io.BytesIO(logo_out)).size) > 120:
                raise ValueError("Encoded logo exceeds the 120x120 limit")

    written: list[Path] = []
    for key in ("site_md", "developer_md"):
        if key not in paths:
            continue
        target = repo_root / paths[key]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            site_markdown(p) if key == "site_md" else developer_markdown(p),
            encoding="utf-8",
        )
        written.append(target)

    screenshot_target = repo_root / paths["screenshot"]
    screenshot_target.parent.mkdir(parents=True, exist_ok=True)
    screenshot_target.write_bytes(reencoded_screenshot)
    written.append(screenshot_target)

    if logo_out is not None:
        logo_target = repo_root / paths["logo"]
        logo_target.parent.mkdir(parents=True, exist_ok=True)
        logo_target.write_bytes(logo_out)
        written.append(logo_target)

    return written


def run(args: list[str]) -> None:
    """Run a subprocess with list args (never a shell) and fail loudly."""
    result = subprocess.run(args, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {args[0]}")


def cmd_publish(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="publish")
    parser.add_argument("stage", choices=["prepare", "pr"])
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--detection", type=Path)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--logo", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    proposal = Proposal.model_validate_json(args.proposal.read_text(encoding="utf-8"))

    if args.stage == "prepare":
        screenshot = args.screenshot.read_bytes()
        # A missing logo artifact is a legitimate no-logo outcome, not an
        # error: write_content_files treats empty bytes as "no logo".
        logo = args.logo.read_bytes() if args.logo and args.logo.exists() else b""
        if args.dry_run:
            for key, rel in output_paths(proposal).items():
                print(f"would write {rel}")
            print(site_markdown(proposal))
            return 0
        written = write_content_files(proposal, args.repo_root, screenshot, logo)
        for path in written:
            print(path)
        return 0

    # Stage "pr": branch, commit, push, PR, issue comment.
    # The PR body links the committed site entry at the pushed HEAD SHA, so
    # the commit must exist (and be pushed) before the body is built.
    if args.detection is None:
        parser.error("publish pr requires --detection")
    detection = json.loads(args.detection.read_text(encoding="utf-8"))
    branch = f"{BRANCH_PREFIX}{proposal.issue_number}"
    paths = output_paths(proposal)
    logo_committed = "logo" in paths and (args.repo_root / paths["logo"]).exists()

    body_file = args.repo_root / ".git" / "PR_BODY.md"

    if args.dry_run:
        # Placeholders keep the advertised local dry-run working without CI
        # env, git writes, or a real push.
        repo = os.environ.get("GITHUB_REPOSITORY", "<GITHUB_REPOSITORY>")
        run_url = os.environ.get("GITHUB_RUN_URL", "<GITHUB_RUN_URL>")
        head_sha = os.environ.get("GITHUB_HEAD_SHA")
        body = build_pr_body(
            proposal, detection, repo, branch, run_url,
            logo_committed=logo_committed, head_sha=head_sha,
        )
        print(f"would create branch {branch} and open a PR on {repo}")
        print(body)
        return 0

    run(["git", "checkout", "-B", branch])
    run(["git", "add", *(str(path) for path in git_add_paths(proposal, args.repo_root))])
    run(["git", "commit", "-m", f"Add site submission from issue #{proposal.issue_number}"])
    # The branch is fully regenerated from validated artifacts each run, so
    # force pushing keeps retries idempotent when the branch (and its PR)
    # already exist from a previous pipeline run. The lease expectation must
    # be explicit: the checkout only fetched the default branch, so no
    # remote-tracking ref exists for --force-with-lease to verify against.
    listing = subprocess.run(
        ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
        check=True, capture_output=True, text=True,
    )
    remote_sha = listing.stdout.split()[0] if listing.stdout.strip() else ""
    run([
        "git", "push",
        f"--force-with-lease=refs/heads/{branch}:{remote_sha}",
        "origin", branch,
    ])

    repo = os.environ["GITHUB_REPOSITORY"]
    run_url = os.environ["GITHUB_RUN_URL"]
    # The commit just pushed is HEAD of the current branch.
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    )
    head_sha = head.stdout.strip()
    entry_line_count = _file_line_count(args.repo_root / paths["site_md"])
    # New-developer submissions also commit a developer profile; the PR
    # body deep-links it with the same file-viewer range.
    profile_line_count = (
        _file_line_count(args.repo_root / paths["developer_md"])
        if not proposal.developer_exists and "developer_md" in paths
        else None
    )
    body = build_pr_body(
        proposal, detection, repo, branch, run_url,
        logo_committed=logo_committed, head_sha=head_sha,
        entry_line_count=entry_line_count,
        profile_line_count=profile_line_count,
    )
    body_file.write_text(body, encoding="utf-8")


    # The PR label may not exist yet in the repository.
    run(["gh", "label", "create", PR_LABEL, "--color", "1d76db", "--force"])
    # A retried submission (issue reopened) may already have an open PR for
    # the branch; update it in place instead of failing.
    result = subprocess.run(
        ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url"],
        check=True, capture_output=True, text=True,
    )
    existing = json.loads(result.stdout or "[]")
    if existing:
        pr_url = existing[0]["url"]
        run(["gh", "pr", "edit", pr_url, "--body-file", str(body_file)])
    else:
        # gh pr create prints the PR URL on stdout — capture it for the issue comment.
        result = subprocess.run(
            ["gh", "pr", "create", "--title", f"New site submission: {proposal.site_title}",
             "--body-file", str(body_file), "--head", branch, "--label", PR_LABEL],
            check=True, capture_output=True, text=True,
        )
        pr_url = result.stdout.strip().splitlines()[-1]
    # Retries must not stack duplicate comments on the issue: edit the
    # bot's most recent comment, creating one only if none exists yet.
    run([
        "gh", "issue", "comment", str(proposal.issue_number),
        "--body", build_pr_comment(proposal, pr_url, run_url),
        "--edit-last", "--create-if-none",
    ])
    run(["gh", "label", "create", PR_CREATED_LABEL, "--color", "0e8a16", "--force"])
    run(["gh", "issue", "edit", str(proposal.issue_number), "--add-label", PR_CREATED_LABEL])
    body_file.unlink(missing_ok=True)
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, *argv = sys.argv[1:]
    try:
        if command == "validate":
            return cmd_validate(argv)
        if command == "render":
            return cmd_render(argv)
        if command == "publish":
            return cmd_publish(argv)
    except Rejection as rejection:  # defensive: cmd_validate already handles it
        print(json.dumps({"reasons": rejection.reasons}), file=sys.stderr)
        return REJECTION_EXIT
    print(f"Unknown command: {command}", file=sys.stderr)
    return ERROR_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
