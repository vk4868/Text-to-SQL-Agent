"""Shared helpers for turning raw model output into runnable SQL."""

CODE_FENCE_OPENINGS = {
    "```sql",
    "```bigquery",
    "```",
}


def clean_sql_output(model_output: str) -> str:
    """Remove an optional Markdown code fence from SQL output.

    Both the SQL generator and the SQL repairer receive free text from
    the model, so the cleaning rule lives here and is shared rather than
    reached into across modules.
    """

    cleaned_output = model_output.strip()

    if not cleaned_output:
        raise ValueError(
            "The model returned an empty SQL response."
        )

    lines = cleaned_output.splitlines()

    if lines and lines[0].strip().lower() in CODE_FENCE_OPENINGS:
        lines = lines[1:]

    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    cleaned_sql = "\n".join(lines).strip()

    if not cleaned_sql:
        raise ValueError(
            "No SQL remained after cleaning the model output."
        )

    return cleaned_sql
