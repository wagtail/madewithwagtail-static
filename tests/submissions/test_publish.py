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
