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

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


def main() -> int:  # wired up in Task 5/8/11
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
