"""PHASE 0108: navigation checker passes on the working tree."""
from scripts.check_navigation import check_navigation, list_xaml_nav_tags


def test_navigation_conforms():
    assert check_navigation() == []
    assert list_xaml_nav_tags() == ["Chat", "Models", "Agents", "Security", "Memory"]
