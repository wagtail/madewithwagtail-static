from pathlib import Path

import pytest

import process_submission as ps

# The lookup functions take the *developers* directory (matching Task 5's
# `--content-dir src/content/developers` default), so point here.
CONTENT = Path(__file__).parent / "fixtures" / "content" / "developers"


class TestMakeSlug:
    def test_basic(self):
        assert ps.make_slug("Rock Kitchen Harris") == "rock-kitchen-harris"

    def test_strips_punctuation(self):
        assert ps.make_slug("Fröjd AB!") == "frojd-ab"

    def test_truncates_to_50(self):
        assert len(ps.make_slug("x" * 200)) == 50

    def test_rejects_reserved(self):
        with pytest.raises(ValueError, match="reserved"):
            ps.make_slug("public")

    def test_rejects_empty_result(self):
        with pytest.raises(ValueError):
            ps.make_slug("///")


class TestDeveloperLookup:
    def test_existing_developers_reads_frontmatter(self):
        devs = ps.existing_developers(CONTENT)
        assert devs["frojd"] == "Fröjd"

    def test_match_developer_exact(self):
        devs = ps.existing_developers(CONTENT)
        assert ps.match_developer("fröjd", devs) == ("frojd", True)

    def test_match_developer_no_match(self):
        devs = ps.existing_developers(CONTENT)
        result = ps.match_developer("Frojd Digital", devs)
        assert isinstance(result, list)


class TestDedup:
    def test_existing_site_origins(self):
        assert ps.existing_site_origins(CONTENT) == {"http://www.visitsweden.com"}

    def test_check_slug_free_site_collision(self):
        with pytest.raises(ValueError, match="already exists"):
            ps.check_slug_free("site", "visit-sweden", CONTENT)

    def test_check_slug_free_new_site_ok(self):
        ps.check_slug_free("site", "brand-new-site", CONTENT)

    def test_check_slug_free_new_developer_ok(self):
        ps.check_slug_free("developer", "brand-new-dev", CONTENT)
