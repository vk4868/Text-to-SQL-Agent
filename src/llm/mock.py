from time import perf_counter

from src.llm.base import LLMResponse

class MockLLMClient:
    """Place a deterministic placeholder for an LLM"""

    def __init__(
        self,
        model_name: str = "mock-gemma",
    ) -> None:
        self.model_name = model_name

    def generate_response(
        self,
        prompt: str,
    ) -> LLMResponse:
        """Return a predictable response without calling a real model."""

        started_at = perf_counter()

        response_text = (
            "This is a placeholder response from the mock Gemma client."
        )

        response_time_ms = round(
            (perf_counter() - started_at) * 1000,
            2,
        )

        return LLMResponse(
            text=response_text,
            model_name=self.model_name,
            response_time_ms=response_time_ms,
        )

