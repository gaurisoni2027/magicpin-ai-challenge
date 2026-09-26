"""
Deterministic validator — post-LLM checks.
Validates the composed message before it goes out.
If it fails, we do ONE repair attempt; otherwise use fallback.
"""
import re
from typing import Optional


VALID_CTAS = {"open_ended", "binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "none"}

# Words we must never send in any message
GLOBAL_TABOO = {"http://", "https://", "www."}


def validate(
    composition: dict,
    trigger: dict,
    merchant: dict,
    category: dict,
    sent_bodies: list[str],
    customer: Optional[dict] = None,
) -> tuple[bool, list[str]]:
    """
    Returns (valid, list_of_failure_reasons).
    An empty failure list means the composition is valid.
    """
    failures = []
    body = composition.get("body", "").strip()
    cta = composition.get("cta", "")
    rationale = composition.get("rationale", "").strip()

    # 1. Body must exist and not be empty
    if not body:
        failures.append("body is empty")

    # 2. CTA must be a known value
    if cta not in VALID_CTAS:
        failures.append(f"invalid cta '{cta}', expected one of {VALID_CTAS}")

    # 3. Rationale must exist
    if not rationale:
        failures.append("rationale is missing")

    # 4. No URLs in body
    for token in GLOBAL_TABOO:
        if token in body:
            failures.append(f"URL detected in body: {token}")
            break

    # 5. Category taboo words
    voice = category.get("voice", {})
    taboos = list(voice.get("vocab_taboo", []) or []) + list(voice.get("taboos", []) or [])
    body_lower = body.lower()
    for taboo in taboos:
        if taboo.lower() in body_lower:
            failures.append(f"taboo word '{taboo}' found in body")

    # 6. Anti-repetition — body must not be identical to a previously sent body
    for prev in sent_bodies:
        if prev.strip() == body.strip():
            failures.append("body is an exact duplicate of a previously sent message")
            break

    # 7. send_as must be set (checked upstream, but validate here too)
    send_as = composition.get("send_as", "vera")
    if send_as not in ("vera", "merchant_on_behalf"):
        failures.append(f"invalid send_as '{send_as}'")

    # 8. Customer consent: if send_as is merchant_on_behalf, customer must exist
    if send_as == "merchant_on_behalf" and customer is None:
        failures.append("send_as=merchant_on_behalf but no customer context")

    return len(failures) == 0, failures
