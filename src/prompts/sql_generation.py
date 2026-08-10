def build_sql_generation_prompt(
    *, 
    question: str,
    schema_document: str,
) -> str:
    """Build a schema grounded prompt for bigquery SQL generation"""

    cleaned_question = question.strip()
    cleaned_schema = schema_document.strip()

    if not cleaned_question:
        raise ValueError("Question cannot be empty.")
    if not cleaned_schema:
        raise ValueError(
            "schema_document cannot be empty"
        )

    return f"""
You are an expert BigQuery SQL analyst.

Your task is to convert the business question into one valid
BigQuery Standard SQL query.

RULES:

1. Use only tables and columns that appear in the supplied schema.
2. Use fully qualified `project.dataset.table` names.
3. Return exactly one read-only query.
4. Use only SELECT statements or SELECT queries with CTEs.
5. Never use INSERT, UPDATE, DELETE, DROP, CREATE, ALTER,
   TRUNCATE, or MERGE.
6. Do not invent table names, column names, or relationships.
7. Do not use SELECT * unless the question explicitly requests
   transaction-level records.
8. Use SAFE_DIVIDE when division by zero is possible.
9. When the question specifies a date period, filter sale_date
   so BigQuery can use partition pruning.
10. Interpret "sales" or "revenue" as net_revenue unless the
    question explicitly asks for total charged including tax.
11. Interpret "profit" as profit_amount.
12. Use clear column aliases suitable for business analysis.
13. Do not include explanations, comments, Markdown, or code fences.
14. Return SQL only.

BUSINESS QUESTION:

{cleaned_question}

AVAILABLE BIGQUERY SCHEMA:

{cleaned_schema}

BIGQUERY SQL:
""".strip()