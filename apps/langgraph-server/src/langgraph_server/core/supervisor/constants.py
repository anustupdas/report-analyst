AGENT_NAME = "supervisor_agent"
SUPERVISOR_NODE = "supervisor"
PROMPT_NAME = "main"
DETAIL_EXTRACTOR_PROMPT = "detail-extractor"
ALLOWED_PROMPT_VARIANTS = frozenset({PROMPT_NAME, DETAIL_EXTRACTOR_PROMPT})
SUPERVISOR_SUMMARIZATION_RUN_NAME = "supervisor.summarization_middleware"

SUPERVISOR_SUMMARIZATION_TRIGGER_MESSAGES = 50
SUPERVISOR_SUMMARIZATION_TRIGGER_TOKENS = 80_000
SUPERVISOR_SUMMARIZATION_KEEP_MESSAGES = 20
SUPERVISOR_SUMMARIZATION_TRIM_TOKENS_TO_SUMMARIZE = 24_000

SUPERVISOR_SUMMARIZATION_PROMPT = """<role>
Conversation Context Compressor
</role>
<objective>
Compress the conversation into a compact context note for future turns.
</objective>
<rules>
- Keep durable facts, decisions, constraints, user preferences, and unresolved questions.
- Exclude plans, next steps, TODOs, workflow instructions, and tool schema boilerplate.
- Keep output concise and high-signal.
</rules>
<output_contract>
- Start with this exact first line: [COMPRESSED_CONTEXT]
- Then write 3 to 8 short bullet points if meaningful context exists.
- If there is truly no meaningful context, output exactly:
[COMPRESSED_CONTEXT]
- None
</output_contract>
<messages>
{messages}
</messages>
Respond only with the compressed context output.
"""
