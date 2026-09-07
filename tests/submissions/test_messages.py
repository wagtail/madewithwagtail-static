import process_submission as ps
from test_proposal import make_proposal_kwargs

DETECTION = {
    "url": "https://example.com",
    "is_wagtail": True,
    "signals": ["generator meta tag"],
    "checked_at": "2026-08-05T00:00:00+00:00",
}


def make_proposal(**overrides):
    return ps.Proposal(**make_proposal_kwargs(**overrides))


class TestPrBody:
    def test_intro_without_heading(self):
        # Regression: the body opened with a duplicate of the PR title
        # heading; it now opens with the issue link line only.
        body = ps.build_pr_body(make_proposal(), DETECTION, "wagtail/madewithwagtail-static", "submission/issue-42", "https://run")
        assert body.splitlines()[0] == (
            "Submission from #42, processed via the [site submission workflow]"
            "(https://github.com/wagtail/madewithwagtail-static/blob/main/CONTRIBUTING.md#site-submissions)."
        )
        assert "## New site submission" not in body

    def test_metadata_table_shape(self):
        body = ps.build_pr_body(
            make_proposal(submission_type="existing-developer", developer_exists=True, developer_slug="torchbox", developer_name="Torchbox", tags=["tourism", "Education"]),
            DETECTION, "wagtail/madewithwagtail-static", "submission/issue-42", "https://run",
        )
        assert "| Field | Value |" in body
        assert "| Site | <https://example.com> |" in body
        assert (
            "| Developer | [Torchbox](https://madewithwagtail.org/developers/torchbox/)"
            " - [see profile page](https://madewithwagtail.org/developers/torchbox/) |" in body
        )
        assert (
            "| Tags | [tourism](https://madewithwagtail.org/sites/tag/tourism/),"
            " [Education](https://madewithwagtail.org/sites/tag/education/) |" in body
        )

    def test_new_developer_suffix(self):
        p = make_proposal()  # new-developer by default
        body = ps.build_pr_body(p, DETECTION, "r/r", "b", "https://run")
        assert "[Example Co](https://madewithwagtail.org/developers/example-co/) - new 🎉" in body

    def test_inline_screenshot_raw_url(self):
        p = make_proposal()
        body = ps.build_pr_body(p, DETECTION, "wagtail/madewithwagtail-static", "submission/issue-42", "https://run")
        assert (
            "https://raw.githubusercontent.com/wagtail/madewithwagtail-static/submission/issue-42/"
            "public/images/example-co/example-site.fill-1200x996.webp" in body
        )

    def test_detection_verdict_detected(self):
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "b", "https://run")
        assert "✅" in body
        assert "generator meta tag" in body

    def test_detection_verdict_not_detected(self):
        detection = {**DETECTION, "is_wagtail": False, "signals": []}
        body = ps.build_pr_body(make_proposal(), detection, "r/r", "b", "https://run")
        assert "⚠️" in body
        assert "### Wagtail detection" in body

    def test_site_entry_link_at_head_sha(self):
        sha = "a" * 40
        body = ps.build_pr_body(
            make_proposal(), DETECTION, "wagtail/madewithwagtail-static", "submission/issue-42", "https://run",
            head_sha=sha, entry_line_count=13,
        )
        assert (
            f"https://github.com/wagtail/madewithwagtail-static/blob/{sha}/"
            "src/content/developers/example-co/example-site/index.md?plain=1#L1-L13" in body
        )

    def test_site_entry_dry_run_without_sha(self):
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "submission/issue-42", "https://run")
        assert "SHA unavailable" in body

    def test_reviewer_checklist_and_footer(self):
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "b", "https://run")
        assert "- [ ]" in body
        assert "https://run" in body
        assert "just serve" in body
        assert "Closes #42" in body

    def test_logo_section_gated_on_logo_committed(self):
        p = make_proposal()  # new-developer: output_paths includes the logo
        with_logo = ps.build_pr_body(p, DETECTION, "r/r", "b", "https://run", logo_committed=True)
        without_logo = ps.build_pr_body(p, DETECTION, "r/r", "b", "https://run", logo_committed=False)
        assert "Developer logo (as committed)" in with_logo
        assert "Developer logo (as committed)" not in without_logo

    def test_logo_section_default_keeps_backward_compatible_behavior(self):
        # None derives from output_paths: a new-developer proposal still
        # advertises the logo unless the caller says otherwise.
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "b", "https://run")
        assert "Developer logo (as committed)" in body


class TestComments:
    def test_pr_comment_links(self):
        text = ps.build_pr_comment(make_proposal(), "https://github.com/r/r/pull/1", "https://run")
        assert "https://github.com/r/r/pull/1" in text
        assert "https://run" in text
        assert "auto-closes" in text

    def test_rejection_comment_lists_reasons(self):
        text = ps.build_rejection_comment(["Fill in the site title.", "Tick the confirmation."], "https://run")
        assert "Fill in the site title." in text
        assert "Tick the confirmation." in text
        assert "https://run" in text

    def test_failure_comment_names_stage(self):
        text = ps.build_failure_comment("render", "screenshot timeout", "https://run")
        assert "render" in text
        assert "screenshot timeout" in text
        assert "needs-triage" in text
