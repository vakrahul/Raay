"""PHASE 0104: shell checker detects malformed XML and missing trees."""
from scripts.check_client_shell import check_client_shell


def test_malformed_xaml_detected(tmp_path):
    (tmp_path / "Views").mkdir()
    (tmp_path / "Bad.xaml").write_text("<Window><Unclosed>", encoding="utf-8")
    (tmp_path / "Views" / "Ok.xaml").write_text("<Page />", encoding="utf-8")
    violations = check_client_shell(tmp_path)
    assert any("Bad.xaml" in v for v in violations)


def test_missing_client_dir_detected(tmp_path):
    violations = check_client_shell(tmp_path / "absent")
    assert any("missing client directory" in v for v in violations)
