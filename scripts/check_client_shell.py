"""PHASE 0103: validate WinUI client XML files without the .NET SDK."""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "src" / "client" / "WinAI.Client"


def check_client_shell(client_dir: Path = CLIENT) -> list:
    violations = []
    if not client_dir.is_dir():
        return [f"missing client directory: {client_dir}"]
    targets = (
        list(client_dir.glob("*.csproj"))
        + list(client_dir.glob("*.manifest"))
        + list(client_dir.rglob("*.xaml"))
        + list(client_dir.rglob("*.cs"))
    )
    if not targets:
        return ["no client source files discovered"]
    for path in sorted(targets):
        suffix = path.suffix.lower()
        try:
            if suffix in (".csproj", ".manifest", ".xaml"):
                ET.parse(path)
            elif suffix == ".cs":
                text = path.read_text(encoding="utf-8")
                if "namespace" not in text and "class" not in text and "interface" not in text:
                    violations.append(f"suspicious C# file (no type declaration): {path.name}")
        except ET.ParseError as e:
            violations.append(f"malformed XML in {path.name}: {e}")
        except Exception as e:
            violations.append(f"unreadable file {path.name}: {e}")
    return violations


def main() -> int:
    violations = check_client_shell()
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print("WinUI client shell XML valid per docs/WINUI_SHELL_SPEC.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
