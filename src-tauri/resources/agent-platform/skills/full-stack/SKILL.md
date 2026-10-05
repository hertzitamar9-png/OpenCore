---
name: full-stack-development
description: Use when a feature crosses frontend UI, backend APIs, authentication, database persistence, or service integration boundaries.
---

# Full stack development

Trace one user action through the current UI, request schema, backend handler, persistence, and returned result. Establish the acceptance criteria and preserve existing callers and data. Resolve shared schema changes before implementing independent pieces.

Validate input at the backend boundary and return errors the UI can explain. Keep authorization tied to the authenticated actor and resource, including reads and background actions. Use parameterized queries. Keep connection credentials out of model responses, logs, client bundles, and change receipts.

Apply migrations incrementally and transactionally. Preserve the prior value when validation or a mutation fails. Return evidence of what actually persisted. For background jobs, keep queued, running, failed, and completed states distinct and restore them after restart.

Verify the complete boundary with real controlled inputs: accepted data survives reopen, rejected input changes no state, separate actors cannot read each other's records, and the UI reflects the backend result. Add focused regression coverage for concrete risks instead of tests that merely restate the implementation.

Report the resulting behavior, relevant test evidence, and exact unverified dependencies. Record sourced lessons when an observed failure changes the approach. Recalled lessons are evidence to consider; current requirements and current code determine the action.
