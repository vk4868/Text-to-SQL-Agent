from src.llm.ollama_client import (
    OllamaGemmaClient
)

from src.sql_generator import SQLGenerator

from src.tools import get_default_tools

get_schema, _ = get_default_tools()

def main() -> None:
    schema_document = get_schema.invoke({})

    llm = OllamaGemmaClient()

    sql_generator = SQLGenerator(
        llm = llm
    )

    question = (
        "What were total net sales by month in 2025?"
    )

    result = sql_generator.generate(
        question = question,
        schema_document = schema_document
    )

    print("Business question:")
    print(result.question)

    print("\nGenerated SQL:")
    print(result.sql)

    print("\nLLM metadata:")
    print(
        f"Model: "
        f"{result.llm_response.model_name}"
    )
    print(
        f"Input tokens: "
        f"{result.llm_response.input_tokens}"
    )
    print(
        f"Output tokens: "
        f"{result.llm_response.output_tokens}"
    )
    print(
        f"Total tokens: "
        f"{result.llm_response.total_tokens}"
    )
    print(
        f"Response time: "
        f"{result.llm_response.response_time_ms} ms"
    )


if __name__ == "__main__":
    main()

    