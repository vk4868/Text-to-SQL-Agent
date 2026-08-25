import json
from typing import Any


def build_result_analysis_prompt(
    *, question: str, 
    sql:str,
    rows: list[dict[str,Any]],
    total_result_rows: int,
    rows_were_truncated: bool
) -> str:
    """ Build a grounded prompt for analysing SQL results"""
    cleaned_question = question.strip()
    cleaned_sql = sql.strip()

    if not cleaned_question:
        raise ValueError(
            "question cannot be empty"
        )
    if not cleaned_sql:
        raise ValueError(
            "sql cannot be empty"
        )
    rows_json = json.dumps(
        rows,
        indent = 2,
        ensure_ascii=False,
        default=str
    )

    return f"""
    You are a senior business and data analyst.

Analyse the supplied BigQuery result and answer the original
business question.

STRICT RULES:

1. Use only the numbers and values contained in the supplied result.
2. Do not invent, estimate, or assume missing values.
3. Do not claim causation when the data only shows an association.
4. Clearly state when the result is empty or insufficient.
5. When rows were truncated, state that the analysis is based only
   on the returned subset.
6. Preserve the meaning of the SQL metrics and aliases.
7. Use concise business language suitable for an analyst,
   finance manager, or decision-maker.
8. Mention important trends, rankings, changes, or unusual values
   only when they are supported by the result.
9. Do not describe the hidden reasoning process.
10. Do not return Markdown tables.

Use this output structure:

DIRECT ANSWER:
A direct response to the business question.

KEY INSIGHTS:
- Two to four important observations supported by the result.

SUPPORTING NUMBERS:
- The most relevant numerical evidence.

LIMITATIONS:
Any important limitation, or "None identified from the supplied result."

SUGGESTED FOLLOW-UP:
One useful analytical follow-up question.

ORIGINAL BUSINESS QUESTION:

{cleaned_question}

SQL USED:

{cleaned_sql}

TOTAL RESULT ROWS:

{total_result_rows}

ROWS WERE TRUNCATED:

{rows_were_truncated}

QUERY RESULT:

{rows_json}

BUSINESS ANALYSIS:
""".strip()