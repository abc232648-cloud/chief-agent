from dataclasses import dataclass
@dataclass
class AIRequest:
    system: str
    user: str
    temperature: float = 0.0
    max_tokens: int | None = None
@dataclass
class AIResponse:
    provider: str
    model: str
    text: str
    fallback_used: bool = False
