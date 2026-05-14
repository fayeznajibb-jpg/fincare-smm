"""
Shared LLM wrapper — Gemini primary (free), Anthropic fallback.

Set GEMINI_API_KEY in .env to use Gemini 2.5 Flash at zero cost.
Falls back to ANTHROPIC_API_KEY if Gemini is unavailable or exhausted.
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
    """Call the best available LLM. Gemini first (free), Anthropic as fallback."""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    gemini_key    = os.getenv("GEMINI_API_KEY")

    # ── Primary: Gemini (free tier) ───────────────────────────────────
    if gemini_key:
        import google.genai as genai
        import google.genai.types as genai_types
        client = genai.Client(api_key=gemini_key)
        config = genai_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
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
                if any(x in str(e) for x in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")):
                    if attempt < len(_RETRY_DELAYS):
                        continue
                # Non-retriable Gemini error — fall through to Anthropic
                break
        else:
            raise last_err  # all retries exhausted

        if anthropic_key:
            import logging
            logging.getLogger("llm").warning(
                f"Gemini failed ({type(last_err).__name__}) — falling back to Anthropic."
            )

    # ── Fallback: Anthropic ───────────────────────────────────────────
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
        "No LLM key found. Add GEMINI_API_KEY or ANTHROPIC_API_KEY to your .env file."
    )
