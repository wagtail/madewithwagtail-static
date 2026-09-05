from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import process_submission as ps


def make_proposal_kwargs(**overrides):
    kwargs = {
        "schema_version": 1,
        "issue_number": 42,
        "submission_type": "new-developer",
        "site_url": "https://example.com",
        "site_title": "Example Site",
        "site_description": "A site.",
        "tags": ["blog"],
        "developer_name": "Example Co",
        "developer_slug": "example-co",
        "site_slug": "example-site",
        "developer_exists": False,
        "submitted_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
    }
    kwargs.update(overrides)
    return kwargs


class TestProposal:
    def test_roundtrip_via_json(self):
        proposal = ps.Proposal(**make_proposal_kwargs())
        restored = ps.Proposal.model_validate_json(proposal.model_dump_json())
        assert restored == proposal

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            ps.Proposal(**make_proposal_kwargs(surprise="x"))

    def test_rejects_bad_slug(self):
        with pytest.raises(ValidationError):
            ps.Proposal(**make_proposal_kwargs(site_slug="../evil"))

    def test_rejects_long_title(self):
        with pytest.raises(ValidationError):
            ps.Proposal(**make_proposal_kwargs(site_title="x" * 81))

    def test_rejects_too_many_tags(self):
        with pytest.raises(ValidationError):
            ps.Proposal(**make_proposal_kwargs(tags=["a", "b", "c", "d", "e", "f"]))

    def test_rejects_bad_github_user(self):
        with pytest.raises(ValidationError):
            ps.Proposal(**make_proposal_kwargs(github_user="not valid!"))
