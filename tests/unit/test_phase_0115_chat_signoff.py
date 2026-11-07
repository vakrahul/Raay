"""PHASE 0115: chat area sign-off via mock round-trip and CLI."""
import subprocess
import sys
from pathlib import Path
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.main import create_app

ROOT = Path(__file__).resolve().parents[2]


def test_chat_contract_cli_exits_zero():
    proc = subprocess.run(
        [sys.executable, "scripts/check_chat_contract.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_mock_chat_roundtrip():
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        res = client.post(
            "/api/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert res.status_code == 200
        assert res.json()["content"]
