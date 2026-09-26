"""
LLM service — thin abstraction over multiple providers.
Returns structured JSON composition from the LLM.
"""
import json
import re
import os
from typing import Optional

from app.config import LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, PROVIDER_DEFAULTS


def _resolve_model() -> str:
    return LLM_MODEL or PROVIDER_DEFAULTS.get(LLM_PROVIDER, "gpt-4o-mini")


def _call_openai(prompt: str, system: str, model: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=LLM_API_KEY)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def _call_anthropic(prompt: str, system: str, model: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=LLM_API_KEY)
    resp = client.messages.create(
        model=model,
        max_tokens=600,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    return resp.content[0].text


def _call_gemini(prompt: str, system: str, model: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=LLM_API_KEY)
    m = genai.GenerativeModel(
        model_name=model,
        system_instruction=system,
        generation_config={"temperature": 0.1, "max_output_tokens": 600},
    )
    resp = m.generate_content(prompt)
    return resp.text


def _call_generic_openai_compat(prompt: str, system: str, model: str, base_url: str) -> str:
    """Used for DeepSeek, Groq (OpenAI-compatible APIs)."""
    from openai import OpenAI
    client = OpenAI(api_key=LLM_API_KEY, base_url=base_url)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=600,
    )
    return resp.choices[0].message.content


def _extract_json(raw: str) -> dict:
    """Extract first JSON object from LLM output."""
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError(f"No JSON found in LLM output: {raw[:200]}")
    return json.loads(match.group())


SYSTEM_PROMPT = """You are Vera, magicpin's AI merchant-growth assistant. 
Compose a short, evidence-grounded WhatsApp message from the provided evidence.

RULES (mandatory):
- Use ONLY facts from the evidence block. Never invent numbers, citations, or offers.
- No URLs in the body text.
- Exactly one CTA in the last sentence.
- Match the category voice (clinical=peer, salon=warm, restaurant=operator, gym=motivational).
- Match the merchant's language preference (Hindi-English mix is fine if indicated).
- Keep body under 200 words; prefer 60-120 words.
- Never re-introduce yourself after turn 1.

RESPOND ONLY with this JSON:
{
  "body": "<WhatsApp message body>",
  "cta": "<open_ended|binary_yes_no|binary_confirm_cancel|none>",
  "rationale": "<1-2 sentences: why this message, what it should achieve>"
}"""


def compose_via_llm(prompt: str) -> dict:
    """
    Call the configured LLM provider and return structured composition.
    Falls back to a ValueError if parsing fails.
    """
    model = _resolve_model()

    provider_calls = {
        "openai":    lambda: _call_openai(prompt, SYSTEM_PROMPT, model),
        "anthropic": lambda: _call_anthropic(prompt, SYSTEM_PROMPT, model),
        "gemini":    lambda: _call_gemini(prompt, SYSTEM_PROMPT, model),
        "deepseek":  lambda: _call_generic_openai_compat(
            prompt, SYSTEM_PROMPT, model, "https://api.deepseek.com/v1"
        ),
        "groq":      lambda: _call_generic_openai_compat(
            prompt, SYSTEM_PROMPT, model, "https://api.groq.com/openai/v1"
        ),
    }

    caller = provider_calls.get(LLM_PROVIDER)
    if not caller:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}")

    raw = caller()
    return _extract_json(raw)


def reply_via_llm(prompt: str) -> dict:
    """
    Call LLM for conversation reply decisions.
    Returns {"action": send|wait|end, "body": ..., "cta": ..., "rationale": ...}
    """
    reply_system = """You are Vera, magicpin's AI merchant-growth assistant, handling a reply in an ongoing WhatsApp conversation.

RULES:
- Reply naturally and move the conversation forward.
- If merchant committed ("ok let's do it", "yes", "go ahead"), switch to ACTION mode immediately — do NOT ask more qualifying questions.
- If merchant declined ("not interested", "stop"), return action=end.
- If message is clearly an auto-reply bot, return action=wait.
- No URLs. No fabricated data. Keep it concise.
- Match language preference (Hinglish OK).

RESPOND ONLY with this JSON:
{
  "action": "send|wait|end",
  "body": "<reply body, required if action=send>",
  "cta": "<open_ended|binary_yes_no|binary_confirm_cancel|none>",
  "wait_seconds": <integer, only if action=wait>,
  "rationale": "<1-2 sentences>"
}"""

    model = _resolve_model()

    provider_calls = {
        "openai":    lambda: _call_openai_reply(prompt, reply_system, model),
        "anthropic": lambda: _call_anthropic_reply(prompt, reply_system, model),
        "gemini":    lambda: _call_gemini_reply(prompt, reply_system, model),
        "deepseek":  lambda: _call_generic_openai_compat(
            prompt, reply_system, model, "https://api.deepseek.com/v1"
        ),
        "groq":      lambda: _call_generic_openai_compat(
            prompt, reply_system, model, "https://api.groq.com/openai/v1"
        ),
    }

    caller = provider_calls.get(LLM_PROVIDER)
    if not caller:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}")

    raw = caller()
    return _extract_json(raw)


def _call_openai_reply(prompt: str, system: str, model: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=LLM_API_KEY)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=400,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def _call_anthropic_reply(prompt: str, system: str, model: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=LLM_API_KEY)
    resp = client.messages.create(
        model=model,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    return resp.content[0].text


def _call_gemini_reply(prompt: str, system: str, model: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=LLM_API_KEY)
    m = genai.GenerativeModel(
        model_name=model,
        system_instruction=system,
        generation_config={"temperature": 0.1, "max_output_tokens": 400},
    )
    resp = m.generate_content(prompt)
    return resp.text
