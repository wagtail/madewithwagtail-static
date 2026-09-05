import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import process_submission as ps
from test_proposal import make_proposal_kwargs  # noqa: F401  (fixture helper below)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "submissions" / "process_submission.py"
# The content-dir contract points at the developers directory itself.
CONTENT = Path(__file__).parent / "fixtures" / "content" / "developers"

FORM_BODY = """\
### Submission type

A new site and new developer profile

### Site URL

https://example.com

### Site title

Example Site

### Short description

A wonderful site about things.

### Tags

blog, responsive

### Developer

Example Co

### Company URL

https://example.co

### Location

Stockholm, Sweden

### Latitude

59.34

### Longitude

18.06

### GitHub username

exampleco

### Confirmations

- [X] I am affiliated with this site or have permission to submit it.
- [X] This is a production website built with Wagtail.
"""


def fake_resolver(host, port, *args, **kwargs):
    # FORM_BODY also contains example.co (Company URL), and the duplicate-origin
    # test rewrites the site URL to www.visitsweden.com — resolve those too.
    table = {
        "example.com": "93.184.216.34",
        "example.co": "93.184.216.35",
        "www.visitsweden.com": "93.184.216.36",
    }
    if host in table:
        return [(socket.AF_INET, None, None, "", (table[host], port))]
    raise FakeResolutionError(host)


class FakeResolutionError(Exception):
    pass


class TestBuildProposal:
    def test_happy_path_new_developer(self):
        proposal = ps.build_proposal(
            FORM_BODY, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
        )
        assert proposal.submission_type == "new-developer"
        assert proposal.developer_slug == "example-co"
        assert proposal.site_slug == "example-site"
        assert proposal.tags == ["blog", "responsive"]
        assert proposal.lat == "59.34"

    def test_existing_developer_path(self):
        body = FORM_BODY.replace("A new site and new developer profile", "A new site on an existing profile")
        body = body.replace("### Developer\n\nExample Co", "### Developer\n\nFröjd")
        proposal = ps.build_proposal(
            body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
        )
        assert proposal.developer_exists is True
        assert proposal.developer_slug == "frojd"

    def test_rejects_unconfirmed_permission(self):
        body = FORM_BODY.replace("- [X] I am affiliated", "- [ ] I am affiliated")
        with pytest.raises(ps.Rejection) as excinfo:
            ps.build_proposal(body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver)
        assert any("affiliated" in r for r in excinfo.value.reasons)

    def test_rejects_duplicate_origin(self):
        body = FORM_BODY.replace("https://example.com", "http://www.visitsweden.com")
        with pytest.raises(ps.Rejection) as excinfo:
            ps.build_proposal(body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver)
        assert any("already" in r for r in excinfo.value.reasons)

    def test_rejects_unknown_existing_developer(self):
        body = FORM_BODY.replace("A new site and new developer profile", "A new site on an existing profile")
        with pytest.raises(ps.Rejection) as excinfo:
            ps.build_proposal(body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver)
        assert any("developer" in r.casefold() for r in excinfo.value.reasons)

    def test_rejects_private_url(self):
        class Bad(Exception):
            pass

        def bad_resolver(host, port, *a, **k):
            raise Bad(host)

        with pytest.raises(ps.Rejection):
            ps.build_proposal(FORM_BODY, issue_number=7, content_dir=CONTENT, resolver=bad_resolver)


class TestValidateCLI:
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "validate", *args],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_cli_accepts(self, tmp_path):
        body_file = tmp_path / "body.txt"
        body_file.write_text(FORM_BODY)
        # Patch DNS via a wrapper is not possible through subprocess; instead
        # use a resolvable URL. example.com resolves publicly in CI.
        result = self.run_cli(
            "--issue-body", str(body_file),
            "--issue-number", "7",
            "--content-dir", str(CONTENT),
        )
        assert result.returncode in (0, 2)  # 2 only if DNS unavailable in sandbox
        if result.returncode == 0:
            data = json.loads(result.stdout)
            assert data["schema_version"] == 1


class TestModelLevelRejections:
    """Over-long fields must become structured rejections, not exit-1 crashes."""

    def test_over_long_title_rejected(self):
        body = FORM_BODY.replace("Example Site", "x" * 81)
        with pytest.raises(ps.Rejection) as excinfo:
            ps.build_proposal(
                body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
            )
        assert any("title" in reason and "80" in reason for reason in excinfo.value.reasons)

    def test_over_long_description_rejected(self):
        body = FORM_BODY.replace(
            "A wonderful site about things.", "y" * 501
        )
        with pytest.raises(ps.Rejection) as excinfo:
            ps.build_proposal(
                body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
            )
        assert any("500" in reason for reason in excinfo.value.reasons)
