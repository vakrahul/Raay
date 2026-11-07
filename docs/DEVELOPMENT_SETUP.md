# REPRODUCIBLE DEVELOPMENT SETUP
## Windows AI Operating Environment (WinAI-OE)
**Document Version:** 1.0.0  
**Phase:** Stage I — Phase 6 (Development Environment)  
**Status:** Approved  

---

## 1. System Requirements
* **Operating System:** Windows 10 (Build 19041+) or Windows 11
* **Python Runtime:** Python 3.12+ (Installed at `C:\Users\RAHUL\AppData\Local\Programs\Python\Python312`)
* **Package Management:** `pip`
* **Desktop Runtime:** .NET 8 / Windows App SDK (WinUI 3)
* **Version Control:** Git 2.46+

---

## 2. Environment Configuration

### Python Environment
Dependencies are specified declaratively in:
* `pyproject.toml` — Standard packaging metadata & tool configurations.
* `requirements.txt` — Production runtime dependencies (FastAPI, Pydantic, Uvicorn, WebSockets, SQLAlchemy, Cryptography).
* `requirements-dev.txt` — Development, linting, and testing dependencies (Pytest, Pytest-AsyncIO, Ruff, Mypy).

### Installation Command
```powershell
python -m pip install -r requirements-dev.txt
```

---

## 3. Tooling & Verification Commands

### Automated Test Suite
```powershell
python -m pytest tests
```

### Targeted Test Categories
```powershell
# Run only isolated unit tests
python -m pytest -m unit

# Run cross-component integration tests
python -m pytest -m integration

# Run adversarial security and penetration tests
python -m pytest -m security
```

### Code Formatting and Linting
```powershell
# Fast linting with Ruff
ruff check src tests

# Format code with Ruff
ruff format src tests
```

---

## 4. Configuration Standards
* Code style is normalized via `.editorconfig` (UTF-8, 4-space indentation for Python/C#, 2-space for JSON/YAML).
* Untracked build outputs, caches, databases, and keys are protected from version control via `.gitignore`.
