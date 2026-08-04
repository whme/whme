from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from readme_updater.sections import Sections, consistency_errors, markers

if TYPE_CHECKING:
    import pytest

REPO_README = Path(__file__).resolve().parents[2] / "README.md"


def _readme(*names: str) -> str:
    """A minimal README declaring the given section markers."""
    return "intro\n" + "\n".join(
        f"<!-- {name}:start -->\n<!-- {name}:end -->" for name in names
    )


class TestMarkers:
    def test_finds_every_declared_section(self) -> None:
        assert markers(_readme("activity", "all_time_language_bar")) == {
            "activity",
            "all_time_language_bar",
        }

    def test_no_markers_when_none_declared(self) -> None:
        assert markers("just prose, no markers") == set()


class TestApply:
    def test_fills_each_marker_with_its_field(self) -> None:
        sections = Sections(
            activity="ACT", recent_language_bar="REC", all_time_language_bar="ALL"
        )
        result = sections.apply(
            _readme("all_time_language_bar", "activity", "recent_language_bar")
        )
        assert "ACT" in result
        assert "REC" in result
        assert "ALL" in result
        # The README's order is honored, not the field order.
        assert result.index("ALL") < result.index("ACT") < result.index("REC")

    def test_skips_markers_absent_from_the_readme(self) -> None:
        sections = Sections(
            activity="ACT", recent_language_bar="REC", all_time_language_bar="ALL"
        )
        # Only the activity marker is present; the others must not raise.
        assert "ACT" in sections.apply(_readme("activity"))


class TestConsistency:
    def test_agreeing_readme_has_no_errors(self) -> None:
        assert consistency_errors(_readme(*Sections.names())) == []

    def test_unknown_marker_is_reported(self) -> None:
        errors = consistency_errors(_readme(*Sections.names(), "made_up"))
        assert any("made_up" in error for error in errors)

    def test_section_missing_from_readme_is_reported(self) -> None:
        errors = consistency_errors(_readme("activity", "recent_language_bar"))
        assert any("all_time_language_bar" in error for error in errors)

    def test_wip_sections_are_exempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Sections, "WIP", frozenset({"all_time_language_bar"}))
        # Missing from the README, but WIP, so no error.
        assert consistency_errors(_readme("activity", "recent_language_bar")) == []


class TestRealReadme:
    def test_the_profile_readme_and_the_code_agree(self) -> None:
        assert consistency_errors(REPO_README.read_text()) == []
