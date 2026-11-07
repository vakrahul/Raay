"""PHASE 0148: enforce accessible names on XAML buttons without .NET SDK."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "src" / "client" / "WinAI.Client" / "Views"


def check_accessibility(views_dir: Path = VIEWS) -> list:
    violations = []
    if not views_dir.is_dir():
        return [f"missing views directory: {views_dir}"]
    for xaml_path in sorted(views_dir.rglob("*.xaml")):
        try:
            text = xaml_path.read_text(encoding="utf-8")
            ET.fromstring(text)
        except ET.ParseError as e:
            violations.append(f"malformed XAML in {xaml_path.name}: {e}")
            continue
        for match in re.finditer(r"<Button\b([^>]*)>", text):
            attrs = match.group(1)
            if "AutomationProperties.Name" not in attrs:
                violations.append(
                    f"button without AutomationProperties.Name in {xaml_path.name}"
                )
                break
    return violations


def main() -> int:
    violations = check_accessibility()
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print("XAML buttons carry accessible names per docs/ACCESSIBILITY_SPEC.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
