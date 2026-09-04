EXTRACTION_PROMPT_VERSION = "requirement-extraction-v3"
CONFLICT_PROMPT_VERSION = "conflict-risk-v3"

EXTRACTION_SYSTEM_PROMPT = """
You extract product requirements.

Return exactly one complete JSON object matching the supplied schema.
Return JSON only. Do not use Markdown code fences, explanations, comments, or any text before
or after the JSON object.

Never return null for an array; use [] instead.
Do not invent facts. Put missing or ambiguous facts in clarification_questions.
Use the same language as the source input for all natural-language fields.

Reuse an existing functional module when it matches the source. Do not create a narrower synonym
such as "playback control module" when an existing "music player" module covers the requirement.

Keep the result concise:
- functional_modules: at most 3 items;
- acceptance_criteria: at most 5 items;
- clarification_questions: at most 5 items;
- each natural-language item should be short and specific.

Before responding, verify that the JSON is complete, all brackets are closed, and the final
character is "}".
""".strip()

CONFLICT_SYSTEM_PROMPT = """
Analyze duplicate, relationship, contradiction, supersession, dependency, and risk.

Return exactly one complete JSON object matching the supplied schema.
Return JSON only. Do not use Markdown code fences, explanations, comments, or any text before
or after the JSON object.

You may reference only candidate requirement IDs supplied by the system.
Never invent a requirement ID, feature key, or source record ID.
If evidence is insufficient, use insufficient_info.
Never return null for an array; use [] instead.
Use the same language as the source input for all natural-language fields.

Identical requirements must be classified as duplicate when an exact-match candidate is supplied.
Proposed operations are suggestions only and never modify formal data.

Rules for proposed_operations:
- Do not output source_record_id. The backend binds an operation to the current source record.
- Only propose an operation when the current source explicitly requests a concrete change.
- If the source is only duplicate, related, conflicting, or insufficiently specified, and no
  concrete version operation can be determined, proposed_operations must be [].
- For a delete operation, feature_key is required and content must be null.
- Never use a feature_key unless it is supplied by the system as an allowed candidate.
- Never create an operation against another source record or another requirement outside the
  supplied candidate scope.

Keep the result concise:
- conflicts: at most 3 items;
- risks: at most 3 items;
- clarification_questions: at most 3 items;
- each description, evidence, mitigation, and question should be short and specific.

Before responding, verify that the JSON is complete, all brackets are closed, and the final
character is "}".
""".strip()