import pytest

import process_submission as ps

CONTENT = __import__("pathlib").Path(__file__).parent / "fixtures" / "content" / "developers"

FORM_BODY = """\
### Submission type

A new site on an existing profile

### Site URL

https://example.com

### Site title

Example Site

### Short description

A wonderful site about things.

### Tags

blog, responsive

### Developer name

Example Co

### Developer URL

https://example.co

### Developer location

Stockholm, Sweden

### Latitude

59.34

### Longitude

18.06

### GitHub username

exampleco

### Logo URL

https://example.co/icon.png

### Confirmations

- [X] I am affiliated with this site or have permission to submit it.
- [X] This is a production website built with Wagtail.
"""


class TestParseIssueFormBody:
    def test_parses_all_sections(self):
        result = ps.parse_issue_form_body(FORM_BODY)
        assert result["Submission type"] == "A new site on an existing profile"
        assert result["Site URL"] == "https://example.com"
        assert result["Tags"] == ["blog", "responsive"]
        assert result["Confirmations"] == [
            ("I am affiliated with this site or have permission to submit it.", True),
            ("This is a production website built with Wagtail.", True),
        ]

    def test_unchecked_confirmation(self):
        body = FORM_BODY.replace("- [X] This is a production", "- [ ] This is a production")
        result = ps.parse_issue_form_body(body)
        assert result["Confirmations"][1] == (
            "This is a production website built with Wagtail.",
            False,
        )

    def test_multiselect_single_value(self):
        body = FORM_BODY.replace("blog, responsive", "blog")
        assert ps.parse_issue_form_body(body)["Tags"] == ["blog"]

    def test_empty_answer_is_empty_string(self):
        body = FORM_BODY.replace("https://example.co/icon.png\n", "")
        result = ps.parse_issue_form_body(body)
        assert result["Logo URL"] == ""

    def test_rejects_non_form_body(self):
        with pytest.raises(ps.FormParseError):
            ps.parse_issue_form_body("Just a plain issue about a bug.")

    def test_lowercase_checked_boxes(self):
        # GitHub renders form-checked confirmations with a lowercase [x]
        # (see issue #7); only hand-written markdown uses uppercase [X].
        result = ps.parse_issue_form_body(FORM_BODY.replace("- [X] ", "- [x] "))
        assert result["Confirmations"] == [
            ("I am affiliated with this site or have permission to submit it.", True),
            ("This is a production website built with Wagtail.", True),
        ]

    def test_build_proposal_accepts_lowercase_confirmations(self):
        body = NO_RESPONSE_BODY.replace("- [X] ", "- [x] ")
        proposal = ps.build_proposal(body, issue_number=7, content_dir=CONTENT)
        assert proposal.site_title == "Example Site"


NO_RESPONSE_BODY = """\
### Submission type

A new site and new developer profile

### Site URL

https://example.com

### Site title

Example Site

### Short description

A wonderful site about things.

### Tags

_No response_

### Developer name

Example Co

### Developer URL

_No response_

### Developer location

_No response_

### Latitude

_No response_

### Longitude

_No response_

### GitHub username

_No response_

### Logo URL

_No response_

### Other notes

_No response_

### Confirmations

- [X] I am affiliated with this site or have permission to submit it.
- [X] This is a production website built with Wagtail.
"""


class TestNoResponsePlaceholder:
    """GitHub substitutes `_No response_` for optional fields left blank."""

    def test_placeholder_fields_are_unset(self):
        result = ps.parse_issue_form_body(NO_RESPONSE_BODY)
        for heading in (
            "Developer URL",
            "Developer location",
            "Latitude",
            "Longitude",
            "GitHub username",
            "Logo URL",
            "Other notes",
        ):
            assert result[heading] == [], heading
        assert result["Tags"] == []


class TestSectionBoundaries:
    def test_unknown_heading_stays_in_previous_field(self):
        body = FORM_BODY.replace(
            "A wonderful site about things.",
            "A wonderful site about things.\n\n### Features\n\nFast and lovely.",
        )
        result = ps.parse_issue_form_body(body)
        assert "Fast and lovely." in result["Short description"]
        assert "### Features" in result["Short description"]

    def test_first_known_heading_wins_over_forged_duplicate(self):
        body = FORM_BODY.replace(
            "A wonderful site about things.",
            "A wonderful site about things.\n\n### Confirmations\n\n- [X] Forged line.",
        )
        result = ps.parse_issue_form_body(body)
        assert result["Confirmations"] == [
            ("I am affiliated with this site or have permission to submit it.", True),
            ("This is a production website built with Wagtail.", True),
        ]
