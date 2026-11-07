"""PHASE 0149: checker flags unnamed buttons and malformed XAML."""
from scripts.check_accessibility import check_accessibility


def test_unnamed_button_detected(tmp_path):
    (tmp_path / "Bad.xaml").write_text(
        "<Page><Button Content=\"Go\" /></Page>", encoding="utf-8"
    )
    violations = check_accessibility(tmp_path)
    assert any("Bad.xaml" in v for v in violations)


def test_malformed_xaml_detected(tmp_path):
    (tmp_path / "Broken.xaml").write_text("<Page><Unclosed>", encoding="utf-8")
    violations = check_accessibility(tmp_path)
    assert any("Broken.xaml" in v for v in violations)
