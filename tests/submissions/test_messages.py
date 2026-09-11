import process_submission as ps
from test_proposal import make_proposal_kwargs

DETECTION = {
    "url": "https://example.com",
    "is_wagtail": True,
    "signals": ["generator meta tag"],
    "technologies": {},
    "checked_at": "2026-08-05T00:00:00+00:00",
}


def make_proposal(**overrides):
    return ps.Proposal(**make_proposal_kwargs(**overrides))


class TestPrBody:
    def test_intro_line(self):
        # The body opens with the close-reference + workflow link; no
        # heading duplicating the PR title. The run link lives inline —
        # the old bottom footer is gone.
        body = ps.build_pr_body(make_proposal(), DETECTION, "wagtail/madewithwagtail-static", "submission/issue-42", "https://run")
        assert body.splitlines()[0] == (
            "Closes #42. Auto-generated PR via the [site submission workflow]"
            "(https://github.com/wagtail/madewithwagtail-static/blob/main/CONTRIBUTING.md#site-submissions)"
            " ([view logs](https://run))."
        )
        assert "## New site submission" not in body
        # The bottom Closes section is gone; the close reference lives in
        # the intro line only.
        assert body.count("Closes #42") == 1

    def test_metadata_table_shape(self):
        body = ps.build_pr_body(
            make_proposal(
                submission_type="existing-developer", developer_exists=True,
                developer_slug="torchbox", developer_name="Torchbox",
                developer_url="https://torchbox.com/", tags=["tourism", "Education"],
            ),
            DETECTION, "wagtail/madewithwagtail-static", "submission/issue-42", "https://run",
        )
        assert "| Field | Value |" in body
        assert "| Site | <https://example.com> |" in body
        # Developer name links to the developer's website; profile link after.
        assert (
            "| Developer | [Torchbox](https://torchbox.com/)"
            " - [see profile page](https://madewithwagtail.org/developers/torchbox/) |" in body
        )
        assert (
            "| Tags | [tourism](https://madewithwagtail.org/sites/tag/tourism/),"
            " [Education](https://madewithwagtail.org/sites/tag/education/) |" in body
        )

    def test_developer_website_link_fallbacks(self):
        # No developer_url + existing profile: the name links to the profile
        # page so the row still works.
        existing = make_proposal(submission_type="existing-developer", developer_exists=True, developer_slug="torchbox", developer_name="Torchbox")
        body = ps.build_pr_body(existing, DETECTION, "r/r", "b", "https://run")
        assert (
            "[Torchbox](https://madewithwagtail.org/developers/torchbox/)"
            " - [see profile page](https://madewithwagtail.org/developers/torchbox/)" in body
        )
        # New developer without a developer_url: plain name, no dead links.
        new = make_proposal()  # new-developer, no developer_url
        body = ps.build_pr_body(new, DETECTION, "r/r", "b", "https://run")
        assert "| Developer | Example Co - new 🎉 |" in body

    def test_submitter_notes_section_when_provided(self):
        p = make_proposal(other_notes="Launched in 2024, redesign of an older site.")
        body = ps.build_pr_body(p, DETECTION, "r/r", "b", "https://run")
        assert "### Submitter notes" in body
        assert "Launched in 2024, redesign of an older site." in body
        # Notes sit before the detected-technologies section and checklist.
        assert body.index("### Submitter notes") < body.index("### Detected technologies")
        assert body.index("### Submitter notes") < body.index("### Reviewer checklist")

    def test_submitter_notes_omitted_when_none(self):
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "b", "https://run")
        assert "### Submitter notes" not in body

    def test_screenshot_is_table_thumbnail(self):
        p = make_proposal()
        body = ps.build_pr_body(p, DETECTION, "wagtail/madewithwagtail-static", "submission/issue-42", "https://run")
        assert (
            '<img src="https://raw.githubusercontent.com/wagtail/madewithwagtail-static/submission/issue-42/'
            'public/images/example-co/example-site.fill-1200x996.webp" width="300" height="249"'
            ' alt="Screenshot of the new site">' in body
        )
        assert "| Screenshot | <img" in body
        assert "### Screenshot" not in body

    def test_detection_in_table(self):
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "b", "https://run")
        assert "| Detection | ✅ generator meta tag |" in body
        assert "### Wagtail detection" not in body
        detection = {**DETECTION, "is_wagtail": False, "signals": []}
        body = ps.build_pr_body(make_proposal(), detection, "r/r", "b", "https://run")
        assert "| Detection | ⚠️ No Wagtail signals detected |" in body

    def test_multiple_signals_joined(self):
        detection = {**DETECTION, "signals": ["generator meta tag", "Wagtail rendition URL in image sources"]}
        body = ps.build_pr_body(make_proposal(), detection, "r/r", "b", "https://run")
        assert "| Detection | ✅ generator meta tag; Wagtail rendition URL in image sources |" in body

    def test_local_preview_in_table(self):
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "b", "https://run")
        assert "| Local preview | `/developers/example-co/example-site` |" in body
        assert "### How to review" not in body
    def test_site_page_raw_url_at_head_sha(self):
        sha = "a" * 40
        body = ps.build_pr_body(
            make_proposal(), DETECTION, "wagtail/madewithwagtail-static", "submission/issue-42", "https://run",
            head_sha=sha, entry_line_count=13,
        )
        assert "### Site page" in body
        # The URL is emitted raw — not wrapped in a markdown link — so the
        # visible text is the URL itself.
        assert (
            f"### Site page\n\nhttps://github.com/wagtail/madewithwagtail-static/blob/{sha}/"
            "src/content/developers/example-co/example-site/index.md?plain=1#L1-L13\n" in body
        )
        assert f"[Site page]({sha}" not in body

    def test_developer_profile_page_section_new_developer(self):
        sha = "b" * 40
        body = ps.build_pr_body(
            make_proposal(), DETECTION, "wagtail/madewithwagtail-static", "submission/issue-42", "https://run",
            head_sha=sha, entry_line_count=13, profile_line_count=9,
        )
        assert "### Developer profile page" in body
        assert (
            f"### Developer profile page\n\nhttps://github.com/wagtail/madewithwagtail-static/blob/{sha}/"
            "src/content/developers/example-co/index.md?plain=1#L1-L9" in body
        )

    def test_no_profile_page_section_existing_developer(self):
        sha = "c" * 40
        body = ps.build_pr_body(
            make_proposal(
                submission_type="existing-developer", developer_exists=True,
                developer_slug="torchbox", developer_name="Torchbox",
            ),
            DETECTION, "wagtail/madewithwagtail-static", "submission/issue-42", "https://run",
            head_sha=sha, entry_line_count=13,
        )
        assert "### Site page" in body
        assert "### Developer profile page" not in body

    def test_site_page_dry_run_without_sha(self):
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "submission/issue-42", "https://run")
        assert "SHA unavailable" in body


    def test_reviewer_checklist_no_footer(self):
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "b", "https://run")
        assert "- [ ]" in body
        # The run link moved into the intro line; the standalone footer
        # must not come back.
        assert "<sub>View the" not in body


    def test_logo_row_gated_on_logo_committed(self):
        p = make_proposal()  # new-developer: output_paths includes the logo
        with_logo = ps.build_pr_body(p, DETECTION, "r/r", "b", "https://run", logo_committed=True)
        without_logo = ps.build_pr_body(p, DETECTION, "r/r", "b", "https://run", logo_committed=False)
        assert "| Logo | <img" in with_logo
        assert "| Logo |" not in without_logo

    def test_logo_row_default_keeps_backward_compatible_behavior(self):
        # None derives from output_paths: a new-developer proposal still
        # advertises the logo unless the caller says otherwise.
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "b", "https://run")
        assert "| Logo | <img" in body

    def test_detected_technologies_section_all_kinds(self):
        detection = {
            **DETECTION,
            "technologies": {
                "incompatible": ["PHP"],
                "complementary": ["React", "Tailwind CSS"],
                "other": ["jQuery"],
            },
        }
        body = ps.build_pr_body(make_proposal(), detection, "r/r", "b", "https://run")
        assert "### Detected technologies" in body
        assert "- ✅ Complementary: React, Tailwind CSS" in body
        assert "- Other: jQuery" in body
        # Incompatible technologies never reach a PR: the workflow's
        # reject-technologies job closes those submissions first.
        assert "PHP" not in body
        # The section sits before the reviewer checklist.
        assert body.index("### Detected technologies") < body.index("### Reviewer checklist")

    def test_detected_technologies_section_empty(self):
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "b", "https://run")
        assert "### Detected technologies" in body
        assert "None detected" in body

    def test_detected_technologies_omitted_from_metadata_table(self):
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "b", "https://run")
        assert "| Detected technologies" not in body


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
