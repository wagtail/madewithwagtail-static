import re

import yaml

import process_submission as ps
from test_proposal import make_proposal_kwargs

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def make_proposal(**overrides):
    return ps.Proposal(**make_proposal_kwargs(**overrides))


def frontmatter_of(text: str) -> dict:
    """Extract the frontmatter block between the --- fences and parse it.

    Regex-based (not split("---")) so values containing '---' cannot
    mis-split the extraction.
    """
    match = FRONTMATTER_RE.match(text)
    assert match, f"No frontmatter block found in: {text[:120]!r}"
    return yaml.safe_load(match.group(1))


class TestSiteMarkdown:
    def test_frontmatter_parses_and_matches(self):
        p = make_proposal(site_description="A wonderful site about things.")
        text = ps.site_markdown(p)
        assert text.startswith("---\n")
        assert frontmatter_of(text) == {
            "title": "Example Site",
            "first_published_at": "2026-08-05T00:00:00+00:00",
            "latest_revision_created_at": "2026-08-05T00:00:00+00:00",
            "site_url": "https://example.com",
            "tags": ["blog"],
        }
        assert text.rstrip().endswith("A wonderful site about things.")

    def test_unset_optionals_are_omitted_not_null(self):
        # Regression: committed frontmatter contained `in_cooperation_with_slug: null`;
        # optional fields left unset must be left out entirely (the Astro
        # schema defaults them).
        text = ps.site_markdown(make_proposal())
        assert "in_cooperation_with_slug" not in text
        assert ": null" not in text

    def test_yaml_injection_resisted(self):
        # A title full of YAML/metacharacters must round-trip through
        # safe_dump intact, never break out of frontmatter.
        p = make_proposal(site_title='Weird: "title" #not comment\n---')
        text = ps.site_markdown(p)
        assert frontmatter_of(text)["title"] == 'Weird: "title" #not comment\n---'

    def test_all_fields_new_developer(self):
        p = make_proposal()
        text = ps.site_markdown(p)
        assert "- blog" in text  # tags as a YAML list

    def test_technologies_written_when_detected(self):
        text = ps.site_markdown(
            make_proposal(),
            {"incompatible": ["PHP"], "complementary": ["React"], "other": ["jQuery"]},
        )
        assert frontmatter_of(text)["technologies"] == ["React"]

    def test_technologies_omitted_when_none(self):
        text = ps.site_markdown(make_proposal())
        assert "technologies" not in text

    def test_technologies_omitted_when_empty_list(self):
        text = ps.site_markdown(make_proposal(), {"complementary": []})
        assert "technologies" not in text


class TestDeveloperMarkdown:
    def test_frontmatter(self):
        p = make_proposal(developer_location="Stockholm, Sweden", github_user="exampleco")
        text = ps.developer_markdown(p)
        frontmatter = frontmatter_of(text)
        assert frontmatter["title"] == "Example Co"
        assert frontmatter["location"] == "Stockholm, Sweden"
        assert "twitter_handler" not in frontmatter
        assert frontmatter["github_user"] == "exampleco"
        assert frontmatter["online_profiles"] == []


class TestOutputPaths:
    def test_paths(self):
        p = make_proposal()
        paths = ps.output_paths(p)
        assert paths["site_md"] == ps.Path("src/content/developers/example-co/example-site/index.md")
        assert paths["developer_md"] == ps.Path("src/content/developers/example-co/index.md")
        assert paths["screenshot"] == ps.Path("public/images/example-co/example-site.fill-1200x996.webp")
        assert paths["logo"] == ps.Path("public/images/example-co/example-co.max-120x120.webp")

    def test_existing_developer_has_no_developer_paths(self):
        p = make_proposal(submission_type="existing-developer", developer_exists=True, developer_slug="frojd")
        paths = ps.output_paths(p)
        assert "developer_md" not in paths
        assert "logo" not in paths
