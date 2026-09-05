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
