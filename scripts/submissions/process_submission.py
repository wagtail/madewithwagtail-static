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
import ipaddress
import json
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

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


def main() -> int:  # render/publish routing wired up in Tasks 8/11/12
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, *argv = sys.argv[1:]
    if command == "validate":
        return cmd_validate(argv)
    print(f"Unknown command: {command}", file=sys.stderr)
    return ERROR_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
