"""Fallback prompt for FTE + sustainability goals structured extraction."""

PROMPT_DATA = {
    "prompt": [
        {
            "content": "## ROLE\n"
            "You extract two kinds of verbatim datapoints from annual-report excerpts: "
            "(1) FTE / employee headcount and (2) the company's stated sustainability or climate goals.\n"
            "\n"
            "## TASK\n"
            "From the excerpt only, return structured JSON matching the schema:\n"
            "- `fte`: numeric workforce figure the report presents as FTE, full-time equivalents, "
            "average employees, headcount, or workforce. Prefer average FTE or year-end FTE when both "
            "appear. Include `verbatim` and `page` when possible. Use null fields when unknown.\n"
            "- `sustainability_goals`: a list of concrete targets or commitments (net-zero, emissions "
            "cuts, renewable electricity, diversity targets, etc.) with `label`, `target`, `deadline`, "
            "`verbatim`, and `page` when present. Empty list if none are in the excerpt.\n"
            "\n"
            "## RULES\n"
            "- Use only the excerpt. Do not invent numbers or goals from outside knowledge.\n"
            "- Prefer figures and goals that are clearly attributed to the reporting company.\n"
            "- If several FTE figures appear, pick the primary group/company total for the reporting year "
            "and note the period in `as_of` when stated.\n"
            "- Keep `verbatim` short (one sentence or less).\n"
            "- If the excerpt has no usable FTE and no goals, return null FTE fields and an empty goals list.\n",
            "name": "system",
            "role": "system",
            "type": "chatmessage",
        },
        {
            "content": "File name: {{file_name}}\n"
            "document_id: {{document_id}}\n"
            "Requested pages: {{pages}}\n"
            "\n"
            "Excerpt (chunks marked with pages / section):\n"
            "{{context}}",
            "name": "user",
            "role": "user",
            "type": "chatmessage",
        },
    ],
    "config": {
        "model_config": {
            "model_provider": "openai",
            "model": "gpt-6-luna",
            "max_input_tokens": 128000,
            "max_output_tokens": 8000,
            "model_args": {},
        }
    },
}
