"""PHASE 0108: canonical navigation destinations and XAML conformance check."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MAIN_XAML = ROOT / "src" / "client" / "WinAI.Client" / "Views" / "MainWindow.xaml"

NAV_DESTINATIONS = ("Chat", "Models", "Agents", "Security", "Memory")


def is_known_destination(tag: str) -> bool:
    return tag in NAV_DESTINATIONS


def list_xaml_nav_tags(xaml_path: Path = MAIN_XAML) -> list:
    text = xaml_path.read_text(encoding="utf-8")
    return re.findall(r'Tag="([^"]+)"', text)


def check_navigation(xaml_path: Path = MAIN_XAML) -> list:
    violations = []
    if not xaml_path.is_file():
        return [f"missing navigation shell: {xaml_path}"]
    tags = list_xaml_nav_tags(xaml_path)
    for tag in tags:
        if not is_known_destination(tag):
            violations.append(f"unknown navigation destination: {tag}")
    for expected in NAV_DESTINATIONS:
        if expected not in tags:
            violations.append(f"missing navigation destination: {expected}")
    return violations


def main() -> int:
    violations = check_navigation()
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print("Navigation destinations conform to docs/NAVIGATION_SPEC.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
