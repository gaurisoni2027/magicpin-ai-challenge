"""
Trigger router — deterministic pre-flight check before LLM is called.

Decides:
1. Is this trigger expired?
2. Is this trigger suppressed?
3. Is the merchant context available?
4. Is the trigger relevant enough to send?
5. Is customer consent respected (for customer-scoped)?

Returns a RouterDecision.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class RouterDecision:
    should_send: bool
    reason: str          # human-readable rationale for the decision
    merchant_id: str
    customer_id: Optional[str]
    send_as: str         # "vera" or "merchant_on_behalf"


def route(
    trigger: dict,
    merchant: Optional[dict],
    category: Optional[dict],
    customer: Optional[dict],
    suppression_store,
    conversation_store,
    now: Optional[str] = None,
) -> RouterDecision:
    """
    Run all deterministic checks.
    Returns RouterDecision with should_send=False if any check fails.
    """
    merchant_id = trigger.get("merchant_id", "")
    customer_id = trigger.get("customer_id")
    kind = trigger.get("kind", "")
    scope = trigger.get("scope", "merchant")
    suppression_key = trigger.get("suppression_key", "")

    # 1. Merchant context must exist
    if not merchant:
        return RouterDecision(False, f"merchant {merchant_id} not in context store", merchant_id, customer_id, "vera")

    # 2. Category context must exist
    if not category:
        cat_slug = merchant.get("category_slug", "?")
        return RouterDecision(False, f"category '{cat_slug}' not in context store", merchant_id, customer_id, "vera")

    # 3. Check expiry against simulated now if provided
    expires_at = trigger.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            ref_now = datetime.fromisoformat(now.replace("Z", "+00:00")) if now else datetime.now(timezone.utc)
            if ref_now.tzinfo is None:
                ref_now = ref_now.replace(tzinfo=timezone.utc)
            if exp < ref_now:
                return RouterDecision(False, f"trigger expired at {expires_at}", merchant_id, customer_id, "vera")
        except (ValueError, TypeError):
            pass

    # 4. Check suppression
    if suppression_key and suppression_store.is_suppressed(suppression_key):
        return RouterDecision(False, f"suppression key '{suppression_key}' already used", merchant_id, customer_id, "vera")

    # 5. Check merchant-level suppression (hostile opt-out)
    if suppression_store.is_merchant_suppressed(merchant_id):
        return RouterDecision(False, f"merchant {merchant_id} has opted out", merchant_id, customer_id, "vera")

    # 6. For customer-scoped triggers, verify customer context and consent
    if scope == "customer":
        if not customer:
            return RouterDecision(False, f"customer {customer_id} not in context store", merchant_id, customer_id, "merchant_on_behalf")

        consent = customer.get("consent", {})
        consent_scope = consent.get("scope", [])
        preferences = customer.get("preferences", {})

        # If customer explicitly opted out of reminders
        if preferences.get("reminder_opt_in") is False:
            return RouterDecision(False, "customer explicitly opted out of reminders", merchant_id, customer_id, "merchant_on_behalf")

        # Map trigger kinds to allowed consent scopes
        consent_map = {
            "recall_due": {"recall_reminders", "appointment_reminders", "reminders"},
            "appointment_tomorrow": {"appointment_reminders", "reminders"},
            "wedding_package_followup": {"bridal_package_followup", "wedding_package_followup", "promotional", "promotional_offers"},
            "customer_lapsed_hard": {"winback_offers", "renewal_reminders", "promotional_offers", "promotional", "recall_reminders"},
            "trial_followup": {"kids_program_updates", "program_updates", "trial_followup", "appointment_reminders"},
            "chronic_refill_due": {"refill_reminders", "delivery_notifications", "recall_alerts"},
        }
        allowed = consent_map.get(kind)
        if allowed:
            if not any(req in consent_scope for req in allowed):
                return RouterDecision(False, f"customer lacks consent for '{kind}'", merchant_id, customer_id, "merchant_on_behalf")
        elif not consent_scope:
            return RouterDecision(False, "customer has empty consent scope", merchant_id, customer_id, "merchant_on_behalf")

        return RouterDecision(True, "customer-scoped trigger, all checks passed", merchant_id, customer_id, "merchant_on_behalf")

    # 7. Subscription check — don't spam lapsed merchants on certain trigger types
    sub = merchant.get("subscription", {})
    sub_status = sub.get("status", "active")
    if sub_status not in ("active", "trial") and kind not in ("renewal_due", "winback_eligible"):
        return RouterDecision(False, f"subscription inactive, skip non-renewal trigger", merchant_id, customer_id, "vera")

    # 8. Minimum evidence check — avoid sending if we have nothing specific to say
    has_evidence = _has_sufficient_evidence(trigger, merchant, category)
    if not has_evidence:
        return RouterDecision(False, "insufficient evidence for a specific message", merchant_id, customer_id, "vera")

    return RouterDecision(True, "all checks passed", merchant_id, customer_id, "vera")


def _has_sufficient_evidence(trigger: dict, merchant: dict, category: dict) -> bool:
    """
    Returns True if there's at least one concrete fact we can anchor the message on.
    Prevents sending a completely generic message.
    """
    kind = trigger.get("kind", "")
    trigger_payload = trigger.get("payload", {})

    # Digest/research/compliance — must have a matching digest item
    if kind in ("research_digest", "regulation_change", "cde_webinar", "cde_opportunity"):
        top_item_id = trigger_payload.get("top_item_id") or trigger_payload.get("digest_item_id")
        digest = category.get("digest", [])
        if top_item_id:
            found = any(d.get("id") == top_item_id for d in digest)
            return found
        # If no specific top_item_id, proceed if category has any digest
        return len(digest) > 0

    # Perf triggers — must have perf data
    if kind in ("perf_dip", "perf_spike", "milestone_reached"):
        perf = merchant.get("performance", {})
        return bool(perf.get("views") or perf.get("calls") or perf.get("ctr"))

    # Renewal — must have days_remaining
    if kind == "renewal_due":
        return trigger_payload.get("days_remaining") is not None

    # Review theme — must have occurrences
    if kind == "review_theme_emerged":
        return trigger_payload.get("occurrences_30d", 0) > 0

    # Festival, IPL, trend — always have payload
    if kind in ("festival_upcoming", "ipl_match_today", "category_trend_movement"):
        return bool(trigger_payload)

    # Recall / appointment — customer context handled upstream
    if kind in ("recall_due", "appointment_tomorrow", "wedding_package_followup"):
        return True

    # Winback — must have days since expiry
    if kind == "winback_eligible":
        return trigger_payload.get("days_since_expiry") is not None

    # Generic / curious ask / dormant — always proceed (evidence is merchant identity)
    return True
