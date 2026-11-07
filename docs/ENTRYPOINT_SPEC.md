# WinAI-OE — FastAPI Entry Point Specification

Status: Normative for STAGE 01 entry-point work (PHASE 0012).

## 1. Startup Contract

- `run_vertical_slice.py` is the single supported local entry script.
- It must obtain `AppConfig` via `get_config()`, build the app with
  `create_app(config)`, and serve `config.host:config.port` via Uvicorn.
- `config.host` must remain a loopback address (`127.0.0.1`); binding to
  `0.0.0.0` or a LAN address is forbidden.

## 2. Route Contract

`create_app()` must register exactly the documented surface:

- `GET /health` — liveness plus provider health.
- `GET /`, `GET /dashboard` — HTML control center.
- `GET /api/v1/system/apps`, `POST /api/v1/system/apps/launch`.
- `GET /api/v1/config` — safe config subset (no secrets).
- `POST /api/v1/tasks/dispatch`, `POST /api/v1/chat`,
  `POST /api/v1/approval/respond`.
- `WS /ws/v1/stream` — full-duplex token/tool stream.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0012_entrypoint_spec.py` passes.
- No route returns secrets; `/api/v1/config` exposes no keys or tokens.
- No change to runtime behavior was required by this phase.
