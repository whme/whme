import pytest

from readme_updater.markup import escape, image, pad, replace_block


class TestMarkupHelpers:
    def test_image_is_a_16px_inline_image(self) -> None:
        assert image("a.svg", alt="x") == (
            '<img src="a.svg" width="16" height="16" alt="x">'
        )

    def test_escape_escapes_markdown_link_brackets(self) -> None:
        assert escape("[cli] go") == "\\[cli\\] go"

    def test_pad_is_a_samp_run_of_non_breaking_spaces(self) -> None:
        assert pad(3) == "<samp>&nbsp;&nbsp;&nbsp;</samp>"

    def test_pad_is_empty_when_zero(self) -> None:
        assert pad(0) == ""


class TestReplaceBlock:
    CONTENT = "before\n<!-- activity:start -->\nstale\n<!-- activity:end -->\nafter\n"

    def test_replaces_marker_delimited_block(self) -> None:
        result = replace_block(self.CONTENT, "activity", "fresh")
        assert result == (
            "before\n<!-- activity:start -->\nfresh\n<!-- activity:end -->\nafter\n"
        )

    def test_is_idempotent(self) -> None:
        once = replace_block(self.CONTENT, "activity", "fresh")
        assert replace_block(once, "activity", "fresh") == once

    def test_raises_on_missing_marker(self) -> None:
        with pytest.raises(ValueError, match="releases"):
            replace_block(self.CONTENT, "releases", "fresh")
