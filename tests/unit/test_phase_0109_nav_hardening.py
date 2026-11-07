"""PHASE 0109: unknown destinations fail closed."""
from scripts.check_navigation import check_navigation, is_known_destination


def test_unknown_destination_rejected():
    assert is_known_destination("Chat") is True
    assert is_known_destination("drop-table") is False
    assert is_known_destination("") is False


def test_missing_destination_detected(tmp_path):
    xaml = tmp_path / "MainWindow.xaml"
    xaml.write_text('<NavigationView><NavigationViewItem Tag="Chat" /></NavigationView>', encoding="utf-8")
    violations = check_navigation(xaml)
    assert any("missing navigation destination: Models" in v for v in violations)
