# Architectural Invariants & Scientific Safety Guardrails

All contributors and AI agents must preserve the following scientific and architectural invariants at all times:

1. **PHA is Not Sentry Linkage:**
   - The NeoWs `is_potentially_hazardous_asteroid` flag is strictly an orbital/size classification.
   - Whether an object is linked to Sentry comes **only** from the identity crosswalk (`bridge_asteroid_identifier`).
   - The NeoWs PHA flag and `is_sentry_object` flag never determine or overwrite the served Sentry status.

2. **No Sentry Record Does Not Mean Safe:**
   - Absence of evidence is not evidence of absence. A missing link can indicate unlinked identity, catalog absence, or ambiguous linkage.
   - The API explicitly returns availability states (`not_resolved`, `not_present`, `ambiguous`, `linked_no_record`, or `available`). Nothing is inferred from absence.

3. **No Synthetic Risk Scores:**
   - Prohibited terms: `danger_score`, `threat_index`, `lethality`, or composite danger ranking indexes.
   - Sentry values must strictly mirror published Mode S summaries.

4. **Unknown is Not False:**
   - Missing or non-applicable values must remain `null` or explicit tri-state booleans (`True`, `False`, `None`). Missing values must never be defaulted to `False` or `0`.

5. **Namespace Isolation:**
   - `neows_id`, `spkid`, and `sentry_id` reside in distinct identifier namespaces and are never interchangeable.
   - Canonical crosswalk keys follow deterministic UUID5: `ast_<UUID5>`.
