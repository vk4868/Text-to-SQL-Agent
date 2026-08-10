"""MANUAL SCRIPT — requires a running Ollama server with OLLAMA_MODEL pulled.

Smoke-checks the LLM layer by sending one short prompt and printing the
response plus token usage.

    python scripts/manual/check_ollama_connection.py
"""

from src.exceptions import LLMProviderError
from src.llm.base import LLMClient
from src.llm.ollama_client import OllamaGemmaClient

def describe_llm_response(
    llm: LLMClient,
) -> None:
    """Test an LLMClient-compatible implementation"""

    prompt = (
        "Return exactly the following text and nothing else: 'test_prompt ok'"

    )

    response = llm.generate_response(prompt)

    print("Ollama Gemma test")
    print("-" * 60)
    print(f"Model: {response.model_name}")
    print(f"Text: {response.text}")
    print(f"Input tokens: {response.input_tokens}")
    print(f"Output tokens: {response.output_tokens}")
    print(f"Total tokens: {response.total_tokens}")
    print(
        f"Response time: "
        f"{response.response_time_ms} ms"
    )

def main() -> None:
    llm = OllamaGemmaClient()

    try:
        describe_llm_response(llm)

    except LLMProviderError as error:
        print("Gemma request failed:")
        print(error)

if __name__ =="__main__":
    main()



    