# Task-to-schedule workflow replay fixtures

Each JSON file validates as `WorkflowReplayCase` and contains:

- the complete workflow request;
- a deterministic fake `TaskDraftV2`;
- expected high-level invariants.

Tests execute these fixtures through the same workflow service while replacing
only the AI gateway. No replay endpoint or real provider call is required.
