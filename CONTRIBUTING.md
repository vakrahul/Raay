# Contributing to WinAI-OE

Thank you for your interest in contributing to **WinAI-OE (Windows AI Operating Environment)**!  
We welcome contributions from developers, researchers, and systems engineers across the globe.

---

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

---

## Architectural Principles (Non-Negotiable)

When contributing code to WinAI-OE, you must adhere strictly to our core architectural mandates:

1. **Zero Implicit Trust:** The AI model is an untrusted advisory component. All actions must be validated, sandboxed, and authorized by the host security engine outside the model's prompt space.
2. **Hardware-Backed Secrecy:** Never store plaintext API keys, tokens, or credentials in files or logs. Always use Windows DPAPI (`CryptProtectData`) via `CredentialVault`.
3. **No Unnecessary Screenshots:** For standard Windows automation, interface directly with the **Windows UI Automation API (`UIAutomationCore`)** and system metadata. Never use remote multimodal LLMs for simple button clicks or text reads.
4. **Verifiable State Execution:** Never mark a task `COMPLETED` based on model intent alone. Every action must verify resulting filesystem states, process PIDs, or accessibility tree values.
5. **Dynamic Control Plane:** System configuration, allowed applications, users, and roles are database-backed (`src/platform/`). Never hardcode admin credentials or permissions in source code.

---

## Development Setup

### 1. Prerequisites
- **Operating System:** Windows 10 or Windows 11 (64-bit)
- **Python:** 3.11 or 3.12
- **Git**

### 2. Clone and Setup Environment
```powershell
# Clone the repository
git clone https://github.com/vakrahul/Raay.git
cd Raay

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install core and development dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 3. Run the Automated Test Suite
Before making changes, verify that the existing test net passes:
```powershell
python -m pytest tests -q
```
All 412+ tests must pass with 100% success rate.

---

## Making Changes & Submitting PRs

1. **Create a Feature Branch:**
   ```powershell
   git checkout -b feature/your-feature-name
   # or for bug fixes:
   git checkout -b fix/issue-description
   ```
2. **Write Clean, Idiomatic Code:**
   - Adhere to PEP 8 standards.
   - Use strict type annotations (`typing`, Pydantic models).
   - Never add unused dependencies.
3. **Add Tests:**
   - Unit tests belong in `tests/unit/`.
   - Cross-boundary and real Windows tests belong in `tests/integration/`.
   - Security and adversarial tests belong in `tests/security/`.
4. **Run Verification:**
   ```powershell
   # Run full test suite
   python -m pytest tests -q
   ```
5. **Commit Conventions:**
   Use conventional commits:
   - `feat(...)`: New feature or capability
   - `fix(...)`: Bug fix
   - `docs(...)`: Documentation changes
   - `test(...)`: Adding or updating tests
   - `refactor(...)`: Code restructuring without behavior change

6. **Submit a Pull Request:**
   - Open a PR against the `main` branch on GitHub.
   - Describe the changes made, architectural alignment, and test output.

---

## Reporting Issues

If you find a bug, vulnerability, or feature request:
- Search existing GitHub Issues to see if it has already been reported.
- If not, create a new Issue with full environment details, steps to reproduce, and expected behavior.
- For security vulnerabilities, review our responsible disclosure policy.
