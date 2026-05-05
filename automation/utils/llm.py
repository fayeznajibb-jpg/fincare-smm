"""
Shared LLM wrapper — routes to Gemini (free) or Anthropic (fallback).

Set GEMINI_API_KEY in .env to use Gemini 2.0 Flash at zero cost.
Falls back to ANTHROPIC_API_KEY if Gemini key is absent.
"""
import os

_GEMINI_MODELS = {
    "sonnet": "gemini-2.0-flash",
    "haiku":  "gemini-2.0-flash-lite",
}
_ANTHROPIC_MODELS = {
    "sonnet": "claude-sonnet-4-6",
    "haiku":  "claude-haiku-4-5-20251001",
}


def call_llm(system: str, prompt: str, tier: str = "sonnet",
             max_tokens: int = 4096) -> str:
    """Call the best available LLM. Returns the response text."""
    gemini_key    = os.getenv("GEMINI_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    if gemini_key:
        import google.genai as genai
        import google.genai.types as genai_types
        client = genai.Client(api_key=gemini_key)
        config = genai_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
        )
        if system:
            config.system_instruction = system
        resp = client.models.generate_content(
            model=_GEMINI_MODELS[tier],
            contents=prompt,
            config=config,
        )
        return resp.text

    if anthropic_key:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
        msg = client.messages.create(
            model=_ANTHROPIC_MODELS[tier],
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    raise EnvironmentError(
        "No LLM key found. Add GEMINI_API_KEY (free at aistudio.google.com) "
        "or ANTHROPIC_API_KEY to your .env file."
    )
