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
    def test_contains_summary_and_closes(self):
        p = make_proposal()
        body = ps.build_pr_body(p, DETECTION, "wagtail/madewithwagtail-static", "submission/issue-42", "https://run")
        assert "Example Site" in body
        assert "https://example.com" in body
        assert "Closes #42" in body

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

    def test_reviewer_checklist_and_footer(self):
        body = ps.build_pr_body(make_proposal(), DETECTION, "r/r", "b", "https://run")
        assert "- [ ]" in body
        assert "https://run" in body
        assert "just serve" in body


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
