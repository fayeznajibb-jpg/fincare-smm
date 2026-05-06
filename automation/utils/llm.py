"""
Shared LLM wrapper — routes to Gemini (free) or Anthropic (fallback).

Set GEMINI_API_KEY in .env to use Gemini 2.5 Flash at zero cost.
Falls back to ANTHROPIC_API_KEY if Gemini key is absent.
"""
import os
import time

_GEMINI_MODELS = {
    "sonnet": "gemini-2.5-flash",
    "haiku":  "gemini-2.5-flash",
}
_ANTHROPIC_MODELS = {
    "sonnet": "claude-sonnet-4-6",
    "haiku":  "claude-haiku-4-5-20251001",
}

_RETRY_DELAYS = [15, 35, 60]  # seconds between retries on 503/429


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
            # Disable thinking to get clean output (no reasoning preamble before JSON)
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        )
        if system:
            config.system_instruction = system

        last_err = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            try:
                resp = client.models.generate_content(
                    model=_GEMINI_MODELS[tier],
                    contents=prompt,
                    config=config,
                )
                return resp.text
            except Exception as e:
                last_err = e
                err_str = str(e)
                # Retry on transient server errors and rate limits
                if any(x in err_str for x in ("503", "UNAVAILABLE", "overloaded", "429", "RESOURCE_EXHAUSTED")):
                    if attempt < len(_RETRY_DELAYS):
                        continue
                raise
        raise last_err

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
