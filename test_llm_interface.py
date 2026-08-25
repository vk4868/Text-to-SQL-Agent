from src.llm.base import LLMClient
from src.llm.mock import MockLLMClient


def run_llm_test(
    llm: LLMClient,
) -> None:
    """Test any object that follows the LLMClient protocol."""

    prompt = "What were total sales by month?"

    response = llm.generate_response(prompt)

    print(f"Model: {response.model_name}")
    print(f"Response: {response.text}")
    print(
        "Response time: "
        f"{response.response_time_ms} ms"
    )
    print(f"Input tokens: {response.input_tokens}")
    print(f"Output tokens: {response.output_tokens}")


def main() -> None:
    llm = MockLLMClient()

    run_llm_test(llm)


if __name__ == "__main__":
    main()