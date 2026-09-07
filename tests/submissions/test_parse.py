import pytest

import process_submission as ps

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

### Developer

Example Co

### Company URL

_No response_

### Location

_No response_

### Latitude

_No response_

### Longitude

_No response_

### GitHub username

_No response_

### Logo URL

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
            "Company URL",
            "Location",
            "Latitude",
            "Longitude",
            "GitHub username",
            "Logo URL",
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
