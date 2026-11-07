# WinAI-OE — Phase Completion Policy

## 1. Mandatory 10-Step Protocol

For every phase:

1. LOAD CONTEXT — read the master roadmap record, implementation status,
   relevant architecture docs, source files, tests, and prior phase reports.
2. CHECK PREREQUISITES — confirm dependencies in
   `docs/ROADMAP_DEPENDENCIES.md`. Stop or resequence if unmet.
3. INSPECT — identify the smallest reasonable change satisfying the phase.
4. PLAN — state what changes, why, affected files, tests, risks, rollback.
5. IMPLEMENT — change only the current phase scope.
6. VERIFY — run relevant tests, inspect actual outputs.
7. REVIEW — review the diff for correctness, maintainability, security.
8. UPDATE DOCUMENTATION — update implementation status and phase report.
9. REPORT — publish the 11-field phase report (see Section 3).
10. STOP — wait for explicit user approval before the next phase.

## 2. Definition of Done

A phase is DONE only when all of the following hold:

- Implementation exists in the working tree.
- Acceptance criteria in the roadmap record are satisfied.
- Required automated tests actually ran and passed.
- Relevant regression checks passed.
- Security implications reviewed; no control weakened.
- Documentation and `docs/IMPLEMENTATION_STATUS.md` updated.
- Working tree inspected (`git status` / `git diff` reviewed).
- Result explainable with evidence (test output, screenshots, logs).

## 3. Required Report Fields

Phase, Title, Status (DONE / BLOCKED / PARTIALLY COMPLETE), Changes,
Files Modified, Tests Executed, Test Results, Security Considerations,
Known Limitations, Acceptance Criteria, Completion Evidence, Rollback
Information.

## 4. Non-Completion States

- BLOCKED: prerequisite, credential, service, or authorization missing.
  State exactly what remains and who must provide it.
- PARTIALLY COMPLETE: some acceptance criteria met. List met vs. unmet
  criteria. Never mark the product production-ready on partial evidence.

## 5. Evidence Rules

- Never claim a test passed unless its output was inspected.
- Never claim an app operation succeeded without verification.
- Never hard-code secrets to satisfy a phase.
- Never weaken security to make a demonstration pass.
- Keep the product runnable after every phase.
