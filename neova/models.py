"""Chat model selection for the two OpenAI-compatible providers."""

from langchain_openai import ChatOpenAI

from .config import _require, get_openrouter_base_url, require_openrouter_api_key


def chat_model(provider: str) -> ChatOpenAI:
    if provider == "openrouter":
        return ChatOpenAI(
            model=_require("CHAT_MODEL", "OpenRouter chat model"),
            api_key=require_openrouter_api_key(),
            base_url=get_openrouter_base_url(),
        )
    if provider == "openai":
        return ChatOpenAI(
            model=_require("OPENAI_CHAT_MODEL", "OpenAI chat model"),
            api_key=_require("OPENAI_API_KEY", "model calls via OpenAI"),
            base_url="https://api.openai.com/v1",
        )
    raise ValueError("Unknown model provider")
