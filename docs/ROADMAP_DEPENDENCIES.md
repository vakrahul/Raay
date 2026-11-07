# WinAI-OE — Roadmap Dependencies

Product: Secure, adaptive, autonomous Windows AI environment (WinAI-OE).
Roadmap: Exactly 1,000 phases in 10 stages x 100 phases.

Canonical build pipeline (deterministic, in order):

1. `python scripts/generate_master_roadmap_1000.py` — generates phase
   content from area definitions (10 focus areas x 5 maturity levels).
2. `python scripts/regroup_roadmap_10x100.py` — regroups the 1,000 phases
   into the approved 10-stage organization without altering phase numbers,
   titles, categories, tasks, tests, or acceptance criteria.
3. Never hand-edit the generated JSON/MD stage numbering; re-run the
   pipeline instead so content and grouping stay in sync.

## Global Ordering Rule

Every phase `PHASE N` depends on `PHASE N-1` unless its record explicitly
states otherwise. No phase may be marked complete while its dependency is
BLOCKED or PARTIALLY COMPLETE without a written waiver in
`docs/IMPLEMENTATION_STATUS.md`.

Advanced autonomous capabilities must never precede their security and
reliability foundations.

## Stage Dependency Chain

- STAGE 01 (0001–0100) Repository Audit, Architecture and Service
  Reliability: no prior stage dependency. Entry point of the program.
  Requires inventory, config validation, and baseline test health before
  architecture hardening begins.
- STAGE 02 (0101–0200) Premium Windows Application and Conversational
  Intelligence: requires STAGE 01 service contracts (health, chat,
  streaming) to be stable.
- STAGE 03 (0201–0300) Persistent Memory, Planning and Task Decomposition:
  requires STAGE 02 conversation contracts to avoid context-schema drift,
  plus context retrieval and provenance tiers before planner work.
- STAGE 04 (0301–0400) Multi-Agent Orchestration and Adaptive Integration:
  requires plan DAG validation, budget estimation, and agent tool profiles;
  execution primitives follow verified design only.
- STAGE 05 (0401–0500) Browser Automation and Windows Desktop Execution:
  requires the adaptation taxonomy plus path/command validation primitives
  and approval gates for external actions.
- STAGE 06 (0501–0600) Software Engineering, Git and Code Review: requires
  plans, verified execution traces, and branch isolation design before
  implementation diffs.
- STAGE 07 (0601–0700) Workflow Automation, Security and Trust: requires
  browser outcomes and credential scoping; no new autonomous capability
  ships without its mapped security gate.
- STAGE 08 (0701–0800) Verification, Recovery, Performance and Cost:
  requires plans, execution traces, and context accounting hooks.
- STAGE 09 (0801–0900) Personalization, Observability and Quality:
  requires provenance tiers, privacy controls, and continuous coverage
  thresholds for release gates.
- STAGE 10 (0901–1000) Production Hardening and Long-Term Evolution:
  requires all STAGES 01–09 acceptance evidence plus hardening sign-off.

## Intra-Stage Ordering

Within each stage, focus areas progress through maturity levels:

- L1 Inventory and current-state audit
- L2 Design specification and acceptance criteria
- L3 Core implementation
- L4 Hardening, edge cases and security review
- L5 Integration, regression tests and sign-off

L3 implementation must not begin before its L1/L2 are recorded. L5
sign-off must not occur while L4 security review is open.

## Duplicate and Prerequisite Checks

The pipeline asserts:

- Exactly 1,000 phase records, IDs PHASE 0001–PHASE 1000, all unique.
- Exactly 100 phases per stage, contiguous ranges.
- All titles unique.
- Each phase declares its dependency string explicitly.
- `payload["stages"] == 10` with a `stage_layout` table.
