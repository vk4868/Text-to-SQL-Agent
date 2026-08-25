"""A deterministic LLM client for tests and offline replay.

MockLLMClient returns one fixed prose string and ignores the prompt, so it
cannot drive anything that needs the model to emit SQL. This client returns
queued responses in order, which is what makes the generator, the repairer,
the analyzer and the whole graph testable without a running model.
"""

from dataclasses import replace

from src.llm.base import LLMResponse


class ScriptedLLMExhaustedError(AssertionError):
    """Raised when more LLM calls are made than the script provides for."""


class ScriptedLLMClient:
    """Return pre-arranged responses in order, one per call.

    Each entry in ``responses`` may be:

    - a ``str``           -> returned as the response text
    - an ``LLMResponse``  -> returned as-is
    - an ``Exception``    -> raised on that call, to simulate provider failure
    """

    def __init__(
        self,
        responses: list[object] | None = None,
        *,
        model_name: str = "scripted-model",
        input_tokens: int = 100,
        output_tokens: int = 20,
        response_time_ms: float = 1.0,
    ) -> None:
        self.responses: list[object] = list(responses or [])
        self.model_name = model_name
        self.default_input_tokens = input_tokens
        self.default_output_tokens = output_tokens
        self.default_response_time_ms = response_time_ms

        # Every prompt this client was handed, so tests can assert on prompt
        # content and on how many calls were made.
        self.prompts: list[str] = []

    @property
    def call_count(self) -> int:
        """Return how many times generate_response has been called."""

        return len(self.prompts)

    def generate_response(self, prompt: str) -> LLMResponse:
        """Return the next scripted response."""

        index = len(self.prompts)
        self.prompts.append(prompt)

        if index >= len(self.responses):
            raise ScriptedLLMExhaustedError(
                f"ScriptedLLMClient received call {index + 1} but was only "
                f"scripted with {len(self.responses)} response(s)."
            )

        scripted = self.responses[index]

        if isinstance(scripted, BaseException):
            raise scripted

        if isinstance(scripted, LLMResponse):
            return scripted

        if not isinstance(scripted, str):
            raise TypeError(
                "Scripted responses must be a str, an LLMResponse, or an "
                f"Exception. Received: {type(scripted).__name__}."
            )

        return LLMResponse(
            text=scripted,
            model_name=self.model_name,
            input_tokens=self.default_input_tokens,
            output_tokens=self.default_output_tokens,
            total_tokens=(
                self.default_input_tokens + self.default_output_tokens
            ),
            response_time_ms=self.default_response_time_ms,
        )

    def with_tokens(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> "ScriptedLLMClient":
        """Return a copy of this client with different default token counts."""

        return ScriptedLLMClient(
            self.responses,
            model_name=self.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            response_time_ms=self.default_response_time_ms,
        )


def scripted_response(
    text: str,
    *,
    model_name: str = "scripted-model",
    input_tokens: int = 100,
    output_tokens: int = 20,
    response_time_ms: float = 1.0,
) -> LLMResponse:
    """Build one LLMResponse with explicit token accounting."""

    return LLMResponse(
        text=text,
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        response_time_ms=response_time_ms,
    )


def with_text(response: LLMResponse, text: str) -> LLMResponse:
    """Return a copy of an LLMResponse carrying different text."""

    return replace(response, text=text)
