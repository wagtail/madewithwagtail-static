import io
import json
from datetime import datetime, timezone

import pytest
from PIL import Image

import process_submission as ps
from test_proposal import make_proposal_kwargs


def make_proposal(**overrides):
    return ps.Proposal(**make_proposal_kwargs(**overrides))


def make_webp(size: tuple[int, int] = (1200, 996)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 10, 10)).save(buf, "WEBP")
    return buf.getvalue()


class TestWriteContentFiles:
    def test_writes_site_and_images(self, tmp_path):
        (tmp_path / "src" / "content" / "developers").mkdir(parents=True)
        (tmp_path / "public" / "images").mkdir(parents=True)
        p = make_proposal()
        written = ps.write_content_files(p, tmp_path, make_webp(), make_webp((200, 100)))
        rel = {str(path.relative_to(tmp_path)) for path in written}
        assert "src/content/developers/example-co/example-site/index.md" in rel
        assert "src/content/developers/example-co/index.md" in rel
        assert "public/images/example-co/example-site.fill-1200x996.webp" in rel
        assert "public/images/example-co.max-120x120.webp" in rel

    def test_existing_developer_writes_less(self, tmp_path):
        (tmp_path / "src" / "content" / "developers").mkdir(parents=True)
        (tmp_path / "public" / "images").mkdir(parents=True)
        p = make_proposal(submission_type="existing-developer", developer_exists=True, developer_slug="frojd")
        written = ps.write_content_files(p, tmp_path, make_webp(), None)
        rel = {path.name for path in written}
        assert rel == {"index.md", "example-site.fill-1200x996.webp"}

    def test_rejects_wrong_dimension_screenshot(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "public").mkdir()
        with pytest.raises(ValueError, match="1200x996"):
            ps.write_content_files(make_proposal(), tmp_path, make_webp((800, 600)), None)

    def test_rejects_non_webp(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "public").mkdir()
        buf = io.BytesIO()
        Image.new("RGB", (1200, 996)).save(buf, "PNG")
        with pytest.raises(ValueError, match="WEBP"):
            ps.write_content_files(make_proposal(), tmp_path, buf.getvalue(), None)


class TestGitAddPaths:
    def test_excludes_missing_logo(self, tmp_path):
        # New-developer proposal whose logo was never written (no logo
        # artifact): the logo path must not reach `git add`.
        p = make_proposal()
        (tmp_path / "src" / "content" / "developers" / "example-co" / "example-site").mkdir(parents=True)
        (tmp_path / "public" / "images" / "example-co").mkdir(parents=True)
        (tmp_path / "src" / "content" / "developers" / "example-co" / "example-site" / "index.md").touch()
        (tmp_path / "src" / "content" / "developers" / "example-co" / "index.md").touch()
        (tmp_path / "public" / "images" / "example-co" / "example-site.fill-1200x996.webp").touch()
        paths = ps.git_add_paths(p, tmp_path)
        assert tmp_path / "public/images/example-co.max-120x120.webp" not in paths
        assert len(paths) == 3

    def test_includes_logo_when_written(self, tmp_path):
        p = make_proposal()
        for rel in ps.output_paths(p).values():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).touch()
        paths = ps.git_add_paths(p, tmp_path)
        assert len(paths) == 4

    def test_existing_developer_paths_only(self, tmp_path):
        p = make_proposal(submission_type="existing-developer", developer_exists=True, developer_slug="frojd")
        for rel in ps.output_paths(p).values():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).touch()
        paths = ps.git_add_paths(p, tmp_path)
        assert {path.name for path in paths} == {"index.md", "example-site.fill-1200x996.webp"}


class TestCommitMessage:
    def test_credits_issue_author(self):
        message = ps.commit_message(make_proposal(), "thibaudcolas", "1234567")
        assert message.startswith("Add site submission from issue #42")
        assert (
            "Co-authored-by: thibaudcolas <1234567+thibaudcolas@users.noreply.github.com>"
            in message
        )

    def test_trailer_set_off_by_blank_line(self):
        message = ps.commit_message(make_proposal(), "thibaudcolas", "1234567")
        assert message.endswith(
            "\n\nCo-authored-by: thibaudcolas <1234567+thibaudcolas@users.noreply.github.com>"
        )

    def test_no_co_author_omits_trailer(self):
        message = ps.commit_message(make_proposal(), None, None)
        assert "Co-authored-by" not in message

    def test_missing_id_omits_trailer(self):
        message = ps.commit_message(make_proposal(), "thibaudcolas", None)
        assert "Co-authored-by" not in message


class TestCmdPublishPrepare:
    def test_missing_logo_file_writes_without_logo(self, tmp_path, capsys):
        """The render job only writes logo.webp when one is discoverable, so
        `--logo rendered/logo.webp` may point at a nonexistent file for a
        new-developer submission. That must produce a logo-less PR, not a
        crash (which would route the submission to needs-triage)."""
        (tmp_path / "repo" / "src" / "content" / "developers").mkdir(parents=True)
        (tmp_path / "repo" / "public" / "images").mkdir(parents=True)
        proposal_file = tmp_path / "proposal.json"
        proposal_file.write_text(make_proposal().model_dump_json())
        screenshot_file = tmp_path / "screenshot.webp"
        screenshot_file.write_bytes(make_webp())

        code = ps.cmd_publish(
            [
                "prepare",
                "--proposal", str(proposal_file),
                "--screenshot", str(screenshot_file),
                "--logo", str(tmp_path / "logo.webp"),  # does not exist
                "--repo-root", str(tmp_path / "repo"),
            ]
        )

        assert code == 0
        out = capsys.readouterr().out
        written = {line for line in out.splitlines() if line.startswith("src/") or line.startswith("public/")}
        assert "public/images/example-co.max-120x120.webp" not in written
        assert (tmp_path / "repo" / "public" / "images" / "example-co" / "example-site.fill-1200x996.webp").exists()
        assert not (tmp_path / "repo" / "public" / "images" / "example-co.max-120x120.webp").exists()
