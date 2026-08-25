def build_sql_repair_prompt(
    *,
    question: str,
    schema_document: str,
    failed_sql: str,
    error_message: str,
    failure_stage: str
) -> str:
    """Build a prompt for repairing a failed Bigquery query"""

    cleaned_question = question.strip()
    cleaned_schema =schema_document.strip()
    cleaned_sql = failed_sql.strip()

    cleaned_error = error_message.strip()

    if not cleaned_question:
        raise ValueError(
            "question cannot be empty"
        )
    if not cleaned_schema:
        raise ValueError(
            "schema document cannot be empty"
        )
    if not cleaned_sql:
        raise ValueError(
            "failed sql cannot be empty"
        )
    if not cleaned_error:
        raise ValueError(
            "error message cannot be empty"
        )
    return f"""
    You are an expert BigQuery SQL analyst repairing a failed query.

Your task is to return one corrected BigQuery Standard SQL query
that answers the original business question.

STRICT RULES:

1. Use only tables and columns in the supplied schema.
2. Use fully qualified `project.dataset.table` names.
3. Return exactly one read-only query.
4. Use only SELECT queries or SELECT queries with CTEs.
5. Never use INSERT, UPDATE, DELETE, DROP, CREATE, ALTER,
   TRUNCATE, or MERGE.
6. Do not invent tables, columns, or relationships.
7. Preserve the original business intent.
8. Correct the specific failure described below.
9. Use sale_date filters when the question specifies a date range.
10. Interpret sales or revenue as net_revenue unless the question
    explicitly asks for total charged including tax.
11. Interpret profit as profit_amount.
12. Use SAFE_DIVIDE when division by zero is possible.
13. Do not include explanations, comments, Markdown, or code fences.
14. Return SQL only.

ORIGINAL BUSINESS QUESTION:

{cleaned_question}

FAILURE STAGE:

{failure_stage}

FAILED SQL:

{cleaned_sql}

ERROR MESSAGE:

{cleaned_error}

AVAILABLE BIGQUERY SCHEMA:

{cleaned_schema}

CORRECTED BIGQUERY SQL:
""".strip()


    
