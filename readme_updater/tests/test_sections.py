"""Tests for the README section template."""

from pathlib import Path

import pytest

from readme_updater.markup import Marker
from readme_updater.sections import _declared_markers, apply, declared_markers

REPO_README = Path(__file__).resolve().parents[2] / "README.md"


def _template(*names: str) -> str:
    """Build a minimal README declaring a balanced block for each name."""
    return "intro\n" + "\n".join(
        f"<!-- {name}:start -->\nold\n<!-- {name}:end -->" for name in names
    )


class TestDeclaredMarkers:
    def test_parses_every_balanced_id(self) -> None:
        assert _declared_markers(_template("activity", "languages")) == {
            "activity",
            "languages",
        }

    @pytest.mark.parametrize(
        "readme", ["<!-- activity:start -->", "<!-- activity:end -->"]
    )
    def test_rejects_an_unbalanced_marker(self, readme: str) -> None:
        with pytest.raises(ValueError, match="unbalanced"):
            _declared_markers(readme)


class TestApply:
    def test_fills_each_marker_in_the_readme_order(self) -> None:
        sections = {Marker.ALL_TIME_LANGUAGE_BAR: "ALL", Marker.ACTIVITY: "ACT"}
        result = apply(sections, _template("activity", "all_time_language_bar"))
        # The README's order wins over the mapping's insertion order.
        assert result.index("ACT") < result.index("ALL")


def test_profile_readme_declares_exactly_the_generated_sections() -> None:
    assert declared_markers(REPO_README.read_text()) == set(Marker)
