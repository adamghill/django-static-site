from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from coltrane.renderer import MistuneMarkdownRenderer


@pytest.fixture
def markdown_renderer():
    return MistuneMarkdownRenderer()


@patch(
    "coltrane.renderer.MistuneMarkdownRenderer._get_markdown_content_as_html",
    return_value=("some-content", None),
)
def test_handle_none_metadata(_get_markdown_content_as_html, markdown_renderer):
    markdown_renderer.get_html_and_markdown("some-slug")


def test_publish_date_in_metadata(markdown_renderer, settings, tmp_path: Path):
    settings.BASE_DIR = tmp_path

    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "test-1.md").write_text(
        """---
template: test-template.html
publish_date: 2022-02-26 10:26:02
---
"""
    )

    (_, metadata) = markdown_renderer.get_html_and_markdown("test-1")

    assert isinstance(metadata["publish_date"], datetime)
    assert metadata["publish_date"] == datetime(2022, 2, 26, 10, 26, 2, 0)  # noqa: DTZ001


def test_draft_true_in_metadata(markdown_renderer, settings, tmp_path: Path):
    settings.BASE_DIR = tmp_path

    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "test-1.md").write_text(
        """---
template: test-template.html
draft: true
---
"""
    )

    (_, metadata) = markdown_renderer.get_html_and_markdown("test-1")

    assert isinstance(metadata["draft"], bool)
    assert metadata["draft"]


def test_draft_false_in_metadata(markdown_renderer, settings, tmp_path: Path):
    settings.BASE_DIR = tmp_path

    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "test-1.md").write_text(
        """---
template: test-template.html
draft: false
---
"""
    )

    (_, metadata) = markdown_renderer.get_html_and_markdown("test-1")

    assert isinstance(metadata["draft"], bool)
    assert not metadata["draft"]


def test_draft_string_in_metadata(markdown_renderer, settings, tmp_path: Path):
    settings.BASE_DIR = tmp_path

    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "test-1.md").write_text(
        """---
template: test-template.html
draft: blob
---
"""
    )

    (_, metadata) = markdown_renderer.get_html_and_markdown("test-1")

    assert isinstance(metadata["draft"], bool)
    assert not metadata["draft"]


def test_draft_1_in_metadata(markdown_renderer, settings, tmp_path: Path):
    settings.BASE_DIR = tmp_path

    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "test-1.md").write_text(
        """---
template: test-template.html
draft: 1
---
"""
    )

    (_, metadata) = markdown_renderer.get_html_and_markdown("test-1")

    assert isinstance(metadata["draft"], bool)
    assert not metadata["draft"]
