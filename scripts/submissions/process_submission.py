#!/usr/bin/env python3
"""Triage showcase site submissions: validate, render, publish.

Subcommands:
    validate --issue-body <file> [--content-dir <dir>]     -> proposal JSON on stdout
    render   --proposal <file> --out-dir <dir> [--url URL] -> detection.json, screenshot.webp, logo.webp
    publish prepare --proposal <file> --detection <file>
              --screenshot <file> [--logo <file>] --repo-root <dir> [--dry-run]
    publish pr      --proposal <file> --repo-root <dir> [--dry-run]

Exit codes: 0 success, 2 rejection (rejection.json written to cwd),
1 unexpected error.
"""

# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.28",
#   "playwright>=1.49",
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
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
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


CHECKBOX_RE = re.compile(r"^- \[(X| )\] (.*)$", re.MULTILINE)


def parse_issue_form_body(body: str) -> dict[str, str | list[str] | list[tuple[str, bool]]]:
    """Parse a GitHub issue form body into {heading: content}.

    GitHub renders form issues as `### <label>` sections. Multiselect
    values arrive comma-separated; confirmations as a checkbox list.
    """
    if "### Submission type" not in body:
        raise FormParseError("Issue body does not look like a site submission form.")

    result: dict[str, str | list[str] | list[tuple[str, bool]]] = {}
    for section in re.split(r"^### ", body, flags=re.MULTILINE)[1:]:
        heading, _, content = section.partition("\n")
        heading = heading.strip()
        content = content.strip()
        if heading == "Confirmations":
            result[heading] = [
                (label.strip(), mark == "X") for mark, label in CHECKBOX_RE.findall(content)
            ]
        elif heading == "Tags":
            result[heading] = [tag.strip() for tag in content.split(",") if tag.strip()]
        else:
            result[heading] = content
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

    if not any(_is_browsable_ip(ip) for ip in ips):
        raise bad(f"Host {host_lower} does not resolve to a public address")

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


GENERATOR_RE = re.compile(
    r"<meta[^>]+name=[\"']generator[\"'][^>]+content=[\"'][^\"']*wagtail",
    re.IGNORECASE,
)
WAGTAIL_ASSET_RE = re.compile(
    r"(?:src|href)=[\"'][^\"']*(?:static/wagtail|wagtailadmin|django-wagtail)",
    re.IGNORECASE,
)
ADMIN_PATHS = ("/admin/", "/cms/", "/cms-admin/")


def detect_wagtail(html: str) -> list[str]:
    """Best-effort Wagtail fingerprints from page HTML (spec: evidence, never a gate)."""
    signals = []
    if GENERATOR_RE.search(html):
        signals.append("generator meta tag")
    if WAGTAIL_ASSET_RE.search(html):
        signals.append("Wagtail asset reference in page source")
    return signals


def probe_admin_pages(client: "httpx.Client", origin: str) -> list[str]:
    """Best-effort admin probe; every failure mode is silently skipped."""
    import httpx

    signals = []
    for path in ADMIN_PATHS:
        try:
            response = client.get(origin + path, follow_redirects=True, timeout=5)
        except httpx.HTTPError:
            continue
        if response.status_code == 200 and "wagtail" in response.text.casefold():
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


def gather_logo_candidates(
    client: "httpx.Client",
    html: str,
    origin: str,
    logo_url: str | None,
    resolver=socket.getaddrinfo,
) -> list[str]:
    """Candidate logo URLs: explicit submission first, then <link> icons,
    then conventional paths. All normalized against the origin and run
    through the same SSRF checks as page fetches — candidates from
    attacker-controlled HTML must never bypass check_public_url."""
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
    add("/apple-touch-icon.png")
    add("/favicon.ico")
    return candidates


def capture_screenshot(url: str, out_path: Path) -> None:
    """Load the URL in headless Chromium and save an encoded WebP screenshot."""
    from playwright.sync_api import sync_playwright

    private_literal_re = re.compile(
        r"^(?:localhost|\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-f:]+\])$", re.IGNORECASE
    )

    def route_guard(route):
        host = route.request.url.split("/")[2].split(":")[0]
        if private_literal_re.fullmatch(host):
            route.abort()
        else:
            route.continue_()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1200, "height": 996}, device_scale_factor=1
        )
        context.route("**/*", route_guard)
        page = context.new_page()
        try:
            page.goto(url, timeout=30_000, wait_until="load")
            page.wait_for_timeout(2000)
            png = page.screenshot(type="png")
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
            for candidate in gather_logo_candidates(client, html, origin, proposal.logo_url):
                try:
                    response = client.get(candidate, timeout=10)
                    if response.status_code != 200:
                        continue
                    # No magic-byte sniffing (it misses ICO favicons): try to
                    # decode + encode with Pillow; any failure means "not a
                    # usable logo". UnidentifiedImageError is an OSError.
                    logo_bytes = encode_logo(response.content)
                    break
                except (httpx.HTTPError, ValueError, OSError):
                    continue

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


def main() -> int:  # publish routing wired up in Tasks 11/12
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, *argv = sys.argv[1:]
    if command == "validate":
        return cmd_validate(argv)
    if command == "render":
        return cmd_render(argv)
    print(f"Unknown command: {command}", file=sys.stderr)
    return ERROR_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
