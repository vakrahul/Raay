# WinAI-OE — Planner Specification

Status: Normative for STAGE 01 planner work (PHASE 0037).

## 1. Plan Contracts

- Plans are DAGs: every dependency must reference an existing subtask;
  self-dependencies and cycles are rejected by `validate_plan_dag()`.
- Subtasks carry explicit `assigned_role`, `allowed_tools`,
  `completion_criteria`, and approval flags.
- The coordinator decomposes goals into inspect → implement → verify steps.

## 2. Factory Rules

- Dynamic spawning is depth-capped and pool-limited under least-privilege:
  children inherit only tools already held by the parent.
- No agent may self-replicate without bound or escalate privileges.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0037_planner_spec.py` passes.
- No plan executes without passing DAG validation first.
