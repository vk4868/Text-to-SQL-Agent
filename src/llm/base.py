from dataclasses import dataclass

from typing import Protocol

@dataclass(frozen=True)
class LLMResponse:
    """Represent the result of one LLM call"""

    text:str
    model_name: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    response_time_ms: float | None = None

class LLMClient(Protocol):
    """Define the behaviour required from an LLM Client"""

    def generate_response(
        self,
        prompt: str,
    ) -> LLMResponse:
        """ Generate a response from a text prompt"""
        ...
    
    