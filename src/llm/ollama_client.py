from time import perf_counter

from httpx import HTTPError
from ollama import Client, ResponseError

from src.config import (
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_TEMPERATURE,
    OLLAMA_TIMEOUT_SECONDS,
)
from src.exceptions import LLMProviderError
from src.llm.base import LLMResponse


class OllamaGemmaClient:
    """Generate responses with a Gemma model served by Ollama."""

    def __init__(
        self,
        *,
        model_name: str = OLLAMA_MODEL,
        host: str = OLLAMA_HOST,
        timeout_seconds: float = OLLAMA_TIMEOUT_SECONDS,
        temperature: float = OLLAMA_TEMPERATURE,
    ) -> None:
        if not model_name.strip():
            raise ValueError(
                "model_name cannot be empty."
            )

        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be greater than zero."
            )

        if not 0 <= temperature <= 2:
            raise ValueError(
                "temperature must be between 0 and 2."
            )

        self.model_name = model_name
        self.host = host
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature

        self.client = Client(
            host=self.host,
            timeout=self.timeout_seconds,
        )

    def generate_response(
        self,
        prompt: str,
    ) -> LLMResponse:
        """Send a prompt to Gemma and return a structured response."""

        cleaned_prompt = prompt.strip()

        if not cleaned_prompt:
            raise ValueError(
                "prompt cannot be empty."
            )

        started_at = perf_counter()

        try:
            response = self.client.generate(
                model=self.model_name,
                prompt=cleaned_prompt,
                stream=False,
                options={
                    "temperature": self.temperature,
                },
            )

        except ResponseError as error:
            status_code = error.status_code

            raise LLMProviderError(
                "Ollama rejected the model request. "
                f"Model: '{self.model_name}'. "
                f"Status code: {status_code}. "
                f"Message: {error.error}"
            ) from error

        except HTTPError as error:
            raise LLMProviderError(
                "Unable to communicate with Ollama at "
                f"'{self.host}'. Confirm that Ollama is running."
            ) from error

        response_time_ms = round(
            (perf_counter() - started_at) * 1000,
            2,
        )

        response_text = (
            response.response or ""
        ).strip()

        if not response_text:
            raise LLMProviderError(
                "Ollama completed the request but returned "
                "an empty response."
            )

        input_tokens = response.prompt_eval_count
        output_tokens = response.eval_count

        total_tokens = None

        if (
            input_tokens is not None
            and output_tokens is not None
        ):
            total_tokens = (
                input_tokens + output_tokens
            )

        return LLMResponse(
            text=response_text,
            model_name=(
                response.model or self.model_name
            ),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            response_time_ms=response_time_ms,
        )