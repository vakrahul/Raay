"""PHASE 0113: verify C# chat model mirrors the Python message contract."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CS_MODEL = ROOT / "src" / "client" / "WinAI.Client" / "Models" / "ChatMessageModel.cs"
PY_BASE = ROOT / "src" / "providers" / "base.py"

PYTHON_ROLES = ("system", "user", "assistant", "tool")


def check_chat_contract(
    cs_path: Path = CS_MODEL, py_path: Path = PY_BASE
) -> list:
    violations = []
    if not cs_path.is_file():
        return [f"missing C# chat model: {cs_path}"]
    if not py_path.is_file():
        return [f"missing Python provider base: {py_path}"]
    cs = cs_path.read_text(encoding="utf-8")
    if "Role" not in cs or "Content" not in cs:
        violations.append("C# ChatMessageModel must declare Role and Content")
    py = py_path.read_text(encoding="utf-8")
    m = re.search(r"pattern=\"([^\"]+)\"", py)
    if not m:
        violations.append("Python ChatMessage role pattern not found")
    else:
        for role in PYTHON_ROLES:
            if role not in m.group(1):
                violations.append(f"Python role pattern missing role: {role}")
    return violations


def main() -> int:
    violations = check_chat_contract()
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print("Chat contracts mirror each other per docs/CHAT_WORKSPACE_SPEC.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
