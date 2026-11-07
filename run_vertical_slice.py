"""Runnable entry point for testing the Phase 10 Vertical Slice directly."""
import asyncio
import httpx
from src.orchestrator.config import get_config
from src.orchestrator.main import create_app
import uvicorn


def create_server_config(config=None):
    """Build the Uvicorn server config from validated AppConfig (no I/O)."""
    resolved = config or get_config()
    return uvicorn.Config(
        app=create_app(resolved),
        host=resolved.host,
        port=resolved.port,
        log_level="info",
    )


async def main():
    config = get_config()
    print(f"[*] Starting WinAI-OE Vertical Slice Orchestrator on {config.host}:{config.port}...")
    server = uvicorn.Server(config=create_server_config(config))
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
