# AI Intake v2 evaluation

Run the scenarios in `task_draft_v2_scenarios.json` through the internal intake
endpoint with a fixed current datetime and timezone. Reviewers score:

- title correctness;
- explicit value preservation;
- source classification;
- relative date normalization;
- duration quality;
- decomposition quality;
- confidence realism;
- confirmation requirements;
- clarification usefulness;
- absence of unnecessary questions.

Recurring tasks are not required to map to a recurrence field. The recurrence
must instead survive in description or an actionable clarification question.
