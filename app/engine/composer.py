"""
Core composer — orchestrates the full pipeline for one trigger:

  Context → Router → Evidence → LLM → Validator → (repair|fallback) → Action

Also exposes compose_reply() for /v1/reply.
"""
import json
import logging
import uuid
from typing import Optional

from app.engine.router import route
from app.engine.evidence import build_evidence
from app.engine.validator import validate
from app.engine.fallback import build_fallback
from app.services.llm import compose_via_llm, reply_via_llm
from app.config import LLM_API_KEY

logger = logging.getLogger(__name__)

# ─── Prompt builder ───────────────────────────────────────────────────────────


def _build_compose_prompt(
    trigger: dict,
    merchant: dict,
    category: dict,
    evidence: dict,
    customer: Optional[dict],
    conversation_history: list[str],
) -> str:
    lang = merchant.get("identity", {}).get("languages", ["en"])
    use_hinglish = "hi" in lang
    lang_note = "Use natural Hindi-English (Hinglish) mix." if use_hinglish else "Use clear English."
    is_customer = trigger.get("scope") == "customer"
    role_note = (
        "You are composing ON BEHALF of the merchant to their customer. sign-off with merchant name, not Vera."
        if is_customer
        else "You are Vera, messaging the merchant directly."
    )
    hist_str = ""
    if conversation_history:
        hist_str = "\nRecent conversation:\n" + "\n".join(
            f"  [vera] {b}" for b in conversation_history[-2:]
        )

    return f"""Compose a WhatsApp message using ONLY the evidence below.

CONTEXT:
{role_note}
{lang_note}
Trigger kind: {trigger.get('kind')}
{hist_str}

EVIDENCE (use only these facts — do not invent anything):
{json.dumps(evidence, ensure_ascii=False, indent=2)}

REQUIREMENTS:
- Anchor on ONE specific fact from the evidence.
- Answer: Why now? Why this merchant? What single action?
- End with exactly one CTA.
- No URLs.
- Max 120 words.
"""


def _build_reply_prompt(
    conv_state,
    merchant_message: str,
    merchant: Optional[dict],
    trigger: Optional[dict],
    category: Optional[dict],
) -> str:
    turns_str = ""
    if conv_state and conv_state.turns:
        for t in conv_state.turns[-4:]:
            turns_str += f"[{t.from_role}] {t.body[:100]}\n"

    merchant_name = ""
    if merchant:
        merchant_name = merchant.get("identity", {}).get("name", "")
    lang = merchant.get("identity", {}).get("languages", ["en"]) if merchant else ["en"]
    use_hinglish = "hi" in lang
    lang_note = "Use natural Hindi-English (Hinglish) mix." if use_hinglish else "Use English."

    trigger_kind = trigger.get("kind", "general") if trigger else "general"
    auto_count = conv_state.auto_reply_count if conv_state else 0

    return f"""Conversation context:
Merchant: {merchant_name}
Trigger kind: {trigger_kind}
Auto-reply count: {auto_count}
{lang_note}

Recent turns:
{turns_str}

Merchant just replied: "{merchant_message}"

Decide the next action. Options:
- action=send: reply naturally and move forward
- action=wait: merchant needs space (auto-reply, "call you back", etc.)
- action=end: merchant declined, hostile, or said stop

Key rules:
- If merchant committed ("ok let's do it", "yes", "go ahead") → action=send, ACTION mode, no more questions.
- If message looks like WhatsApp Business auto-reply ("Thank you for contacting...") → note auto_reply_count={auto_count}.
  - First auto-reply: send one light ping for the owner.
  - Second: wait.
  - Third+: end.
- If hostile/stop/not interested → end.
- Off-topic (GST, unrelated): politely decline and redirect to the original task.
"""


# ─── Main compose function ────────────────────────────────────────────────────


def compose(
    trigger_id: str,
    trigger: dict,
    merchant: dict,
    category: dict,
    customer: Optional[dict],
    suppression_store,
    conversation_store,
    now: Optional[str] = None,
) -> Optional[dict]:
    """
    Full pipeline: route → evidence → LLM → validate → (repair|fallback).
    Returns an action dict or None if nothing should be sent.
    """
    merchant_id = trigger.get("merchant_id", "")
    customer_id = trigger.get("customer_id")
    suppression_key = trigger.get("suppression_key", "")
    kind = trigger.get("kind", "")

    # ── 1. Router (deterministic pre-check) ───────────────────────────────────
    decision = route(trigger, merchant, category, customer, suppression_store, conversation_store, now=now)
    if not decision.should_send:
        logger.debug("Trigger %s skipped: %s", trigger_id, decision.reason)
        return None

    send_as = decision.send_as

    # ── 2. Build evidence ─────────────────────────────────────────────────────
    evidence = build_evidence(trigger, merchant, category, customer)

    # ── 3. LLM compose ────────────────────────────────────────────────────────
    conv_id = f"conv_{merchant_id}_{trigger_id}_{_short_id()}"
    prev_state = conversation_store.get(conv_id)
    sent_bodies = conversation_store.get_sent_bodies(conv_id) if prev_state else []

    composition = None
    if LLM_API_KEY:
        try:
            prompt = _build_compose_prompt(
                trigger, merchant, category, evidence, customer, sent_bodies
            )
            raw = compose_via_llm(prompt)
            raw["send_as"] = send_as
            is_valid, failures = validate(raw, trigger, merchant, category, sent_bodies, customer)

            if is_valid:
                composition = raw
            else:
                logger.warning("Validation failed for %s: %s. Attempting repair.", trigger_id, failures)
                # ONE repair: add failures to prompt
                repair_prompt = prompt + f"\n\nPrevious attempt failed because: {failures}. Please fix."
                raw2 = compose_via_llm(repair_prompt)
                raw2["send_as"] = send_as
                is_valid2, failures2 = validate(raw2, trigger, merchant, category, sent_bodies, customer)
                if is_valid2:
                    composition = raw2
                else:
                    logger.warning("Repair also failed (%s). Using fallback.", failures2)
        except Exception as e:
            logger.warning("LLM error for %s: %s. Using fallback.", trigger_id, e)

    # ── 4. Fallback if LLM failed or not configured ───────────────────────────
    if composition is None:
        fb = build_fallback(trigger, merchant, category, customer)
        fb["send_as"] = send_as
        composition = fb

    # ── 5. Build final action ─────────────────────────────────────────────────
    body = composition["body"]
    # Prevent identical body in same conversation
    if body in sent_bodies:
        body = f"{body} (Follow-up note)"

    cta = composition.get("cta", "open_ended")
    rationale = composition.get("rationale", "")

    template_name = _template_name(kind, send_as)
    template_params = _template_params(trigger, merchant, category, customer, body)

    action = {
        "conversation_id": conv_id,
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "send_as": send_as,
        "trigger_id": trigger_id,
        "template_name": template_name,
        "template_params": template_params,
        "body": body,
        "cta": cta,
        "suppression_key": suppression_key,
        "rationale": rationale,
    }

    # ── 6. Register state ─────────────────────────────────────────────────────
    conversation_store.get_or_create(
        conv_id, merchant_id, customer_id, trigger_id, suppression_key
    )
    conversation_store.add_turn(conv_id, "vera", body, 1)
    if suppression_key:
        suppression_store.suppress(suppression_key, conv_id, merchant_id)

    return action


# ─── Reply composer ────────────────────────────────────────────────────────────


def compose_reply(
    conversation_id: str,
    merchant_id: str,
    customer_id: Optional[str],
    merchant_message: str,
    turn_number: int,
    conversation_store,
    suppression_store,
    context_store,
) -> dict:
    """
    Handle an incoming reply and decide: send | wait | end.
    """
    conv = conversation_store.get_or_create(conversation_id, merchant_id, customer_id)

    # ── Detect auto-reply ─────────────────────────────────────────────────────
    if _is_auto_reply(merchant_message):
        auto_count = conversation_store.increment_auto_reply(conversation_id, merchant_id)

        if auto_count >= 3:
            conversation_store.mark_ended(conversation_id)
            suppression_store.mark_ended(conversation_id)
            return {
                "action": "end",
                "rationale": f"Auto-reply detected {auto_count}x in a row; no real engagement. Closing conversation.",
            }
        elif auto_count == 2:
            conversation_store.set_intent(conversation_id, "waiting")
            return {
                "action": "wait",
                "wait_seconds": 1800,
                "rationale": "Same auto-reply twice in a row — owner not at phone. Waiting 30 minutes.",
            }
        else:
            # First auto-reply: send one light ping for the owner
            conversation_store.add_turn(conversation_id, "merchant", merchant_message, turn_number)
            body = "Looks like an auto-reply 😊 When the owner sees this, just reply 'Yes' to continue."
            conversation_store.add_turn(conversation_id, "vera", body, turn_number + 1)
            return {
                "action": "send",
                "body": body,
                "cta": "binary_yes_no",
                "rationale": "Detected first auto-reply; one prompt for the owner before backing off.",
            }

    # ── Detect decline / stop ──────────────────────────────────────────────────
    if _is_decline(merchant_message):
        conversation_store.mark_ended(conversation_id)
        suppression_store.mark_ended(conversation_id)
        suppression_store.suppress_merchant(merchant_id, 86400 * 30)
        return {
            "action": "end",
            "rationale": "Merchant explicitly declined or said stop. Closing conversation; suppressing merchant for 30 days.",
        }

    # ── Detect commitment / intent transition ──────────────────────────────────
    if _is_commitment(merchant_message):
        conversation_store.set_intent(conversation_id, "committed")

    # ── Add turn to history ────────────────────────────────────────────────────
    conversation_store.add_turn(conversation_id, "merchant", merchant_message, turn_number)

    # ── LLM reply ──────────────────────────────────────────────────────────────
    merchant = context_store.get("merchant", merchant_id)
    trigger_id = conv.trigger_id
    trigger = context_store.get("trigger", trigger_id) if trigger_id else None
    category_slug = merchant.get("category_slug", "") if merchant else ""
    category = context_store.get("category", category_slug) if category_slug else None

    if LLM_API_KEY:
        try:
            prompt = _build_reply_prompt(conv, merchant_message, merchant, trigger, category)
            result = reply_via_llm(prompt)

            action = result.get("action", "send")
            if conv.intent == "committed":
                action = "send"

            response = {"action": action, "rationale": result.get("rationale", "")}
            if action == "send":
                body = result.get("body", "")
                if not body:
                    body = "Got it — proceeding with the next steps now. I'll draft the campaign and confirm details shortly."
                # If committed, guarantee action mode without qualifying phrases
                if conv.intent == "committed":
                    qualifying = ["would you", "do you", "can you tell", "what if", "how about"]
                    actioning = ["done", "sending", "draft", "here", "confirm", "proceed", "next"]
                    body_lower = body.lower()
                    if any(w in body_lower for w in qualifying) or not any(w in body_lower for w in actioning):
                        body = "Got it — proceeding with the next steps now. I will draft the campaign and confirm the details for you right here."

                cta = result.get("cta", "open_ended")
                # Remove any URLs
                import re as _re
                body = _re.sub(r"https?://\S+", "", body).strip()
                response["body"] = body
                response["cta"] = cta
                conversation_store.add_turn(conversation_id, "vera", body, turn_number + 1)
            elif action == "wait":
                response["wait_seconds"] = result.get("wait_seconds", 1800)
                conversation_store.set_intent(conversation_id, "waiting")
            elif action == "end":
                conversation_store.mark_ended(conversation_id)
                suppression_store.mark_ended(conversation_id)

            return response

        except Exception as e:
            logger.warning("LLM reply error for conv %s: %s. Using deterministic fallback.", conversation_id, e)

    # ── Deterministic fallback for reply ──────────────────────────────────────
    return _deterministic_reply(conv, merchant_message)


def _deterministic_reply(conv, message: str) -> dict:
    """Fallback reply when LLM is unavailable."""
    if conv.intent == "committed":
        body = "Got it — proceeding with the next steps now. I will draft the campaign and confirm the details for you right here."
        return {"action": "send", "body": body, "cta": "open_ended", "rationale": "Merchant committed; switched to action mode."}

    if any(w in message.lower() for w in ["yes", "sure", "okay", "ok", "go ahead", "do it"]):
        return {
            "action": "send",
            "body": "Great! Working on it now — will share the draft shortly.",
            "cta": "open_ended",
            "rationale": "Positive signal; proceeding.",
        }

    return {
        "action": "send",
        "body": "Understood! Anything else I can help you with for your business?",
        "cta": "open_ended",
        "rationale": "Generic continuation.",
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


def _is_auto_reply(message: str) -> bool:
    msg = message.lower()
    patterns = [
        "thank you for contacting",
        "our team will respond",
        "automated",
        "auto-reply",
        "autoreply",
        "aapki jaankari ke liye",
        "main aapki",
        "main ek automated",
        "shukriya",
        "team tak pahuncha",
    ]
    # Consider it auto-reply if 2+ patterns match, or the classic template matches exactly
    matches = sum(1 for p in patterns if p in msg)
    if matches >= 2:
        return True
    # Also: very short messages that are clearly canned
    if "thank you for contacting" in msg and ("respond" in msg or "team" in msg):
        return True
    return False


def _is_decline(message: str) -> bool:
    msg = message.lower()
    decline_phrases = [
        "not interested", "stop messaging", "stop sending", "do not contact",
        "don't contact", "remove me", "opt out", "unsubscribe",
        "why are you bothering", "this is spam", "useless spam",
        "stop", "nahi chahiye", "band karo", "mat bhejo",
    ]
    return any(p in msg for p in decline_phrases)


def _is_commitment(message: str) -> bool:
    msg = message.lower()
    commitment_phrases = [
        "let's do it", "lets do it", "ok let's", "ok lets",
        "go ahead", "yes please", "yes, go", "please go ahead",
        "confirm", "proceed", "do it", "start", "chalega", "haan karo",
        "what's next", "whats next",
    ]
    return any(p in msg for p in commitment_phrases)


def _template_name(kind: str, send_as: str) -> str:
    if send_as == "merchant_on_behalf":
        return f"merchant_{kind}_v1"
    kind_map = {
        "research_digest": "vera_research_digest_v1",
        "regulation_change": "vera_compliance_alert_v1",
        "perf_dip": "vera_perf_dip_v1",
        "perf_spike": "vera_perf_spike_v1",
        "recall_due": "merchant_recall_reminder_v1",
        "renewal_due": "vera_renewal_due_v1",
        "festival_upcoming": "vera_festival_v1",
        "review_theme_emerged": "vera_review_theme_v1",
        "dormant_with_vera": "vera_reengagement_v1",
        "winback_eligible": "vera_winback_v1",
        "ipl_match_today": "vera_ipl_promo_v1",
    }
    return kind_map.get(kind, f"vera_{kind}_v1")


def _template_params(trigger, merchant, category, customer, body) -> list[str]:
    identity = merchant.get("identity", {})
    name = identity.get("name", "")
    owner = identity.get("owner_first_name", "")
    params = [owner or name]

    kind = trigger.get("kind", "")
    tpay = trigger.get("payload", {})

    if kind in ("research_digest", "regulation_change"):
        top_id = tpay.get("top_item_id", "")
        digest = category.get("digest", [])
        item = next((d for d in digest if d.get("id") == top_id), None)
        if item:
            params.append(item.get("source", ""))
            params.append(item.get("title", "")[:60])

    elif kind in ("recall_due", "wedding_package_followup", "customer_lapsed_hard", "trial_followup", "chronic_refill_due", "appointment_tomorrow") and customer:
        cust_name = customer.get("identity", {}).get("name", "")
        params = [cust_name, name, str(tpay.get("service_due", tpay.get("intent_topic", kind)))]
        slots = tpay.get("available_slots", tpay.get("next_session_options", []))
        if slots:
            params.append(" or ".join(s.get("label", "") for s in slots[:2]))

    elif kind in ("perf_dip", "perf_spike", "seasonal_perf_dip"):
        perf = merchant.get("performance", {})
        params.append(tpay.get("metric", "metric"))
        params.append(str(perf.get("ctr", "?")))

    elif kind == "renewal_due":
        params.append(str(tpay.get("days_remaining", "?")))
        params.append(tpay.get("plan", "Pro"))

    elif kind == "festival_upcoming":
        params.append(tpay.get("festival", "festival"))

    elif kind == "ipl_match_today":
        params.append(tpay.get("match", "IPL"))

    # Always add a body snippet as last param
    params.append(body[:80])
    return params
