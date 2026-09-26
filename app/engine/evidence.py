"""
Evidence builder — constructs a compact, LLM-ready evidence object
from the four context layers for a given trigger.

Only includes facts actually present in context.
The LLM must never invent data that isn't in evidence.
"""
from typing import Optional
import json


def build_evidence(
    trigger: dict,
    merchant: dict,
    category: dict,
    customer: Optional[dict] = None,
) -> dict:
    """
    Returns a compact evidence dict with only trigger-relevant facts.
    """
    kind = trigger.get("kind", "")
    evidence: dict = {}

    # ── Merchant identity facts ──────────────────────────────────────────────
    identity = merchant.get("identity", {})
    merchant_facts = [
        f"Name: {identity.get('name', 'Unknown')}",
        f"City: {identity.get('city', '?')}, {identity.get('locality', '?')}",
        f"Languages: {identity.get('languages', ['en'])}",
    ]
    if identity.get("owner_first_name"):
        merchant_facts.append(f"Owner first name: {identity['owner_first_name']}")
    if identity.get("verified"):
        merchant_facts.append("GBP: verified")
    else:
        merchant_facts.append("GBP: NOT verified")
    evidence["merchant_facts"] = merchant_facts

    # ── Performance facts ────────────────────────────────────────────────────
    perf = merchant.get("performance", {})
    perf_facts = []
    if perf:
        perf_facts.append(
            f"30d: views={perf.get('views','?')}, calls={perf.get('calls','?')}, "
            f"ctr={perf.get('ctr','?')}, directions={perf.get('directions','?')}"
        )
        d7 = perf.get("delta_7d", {})
        if d7:
            perf_facts.append(
                f"7d delta: views {_pct(d7.get('views_pct'))}, calls {_pct(d7.get('calls_pct'))}"
            )
    peer = category.get("peer_stats", {})
    if peer.get("avg_ctr") and perf.get("ctr"):
        merchant_ctr = perf["ctr"]
        peer_ctr = peer["avg_ctr"]
        if merchant_ctr < peer_ctr:
            perf_facts.append(f"CTR BELOW PEER: merchant {merchant_ctr:.3f} vs peer median {peer_ctr:.3f}")
        else:
            perf_facts.append(f"CTR above peer: merchant {merchant_ctr:.3f} vs peer median {peer_ctr:.3f}")
    evidence["performance_facts"] = perf_facts

    # ── Active offers ────────────────────────────────────────────────────────
    active_offers = [
        o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"
    ]
    evidence["active_offers"] = active_offers

    # ── Signals ─────────────────────────────────────────────────────────────
    evidence["signals"] = merchant.get("signals", [])

    # ── Subscription ────────────────────────────────────────────────────────
    sub = merchant.get("subscription", {})
    evidence["subscription"] = {
        "status": sub.get("status", "?"),
        "plan": sub.get("plan", "?"),
        "days_remaining": sub.get("days_remaining"),
    }

    # ── Customer aggregate ───────────────────────────────────────────────────
    ca = merchant.get("customer_aggregate", {})
    if ca:
        evidence["customer_aggregate"] = ca

    # ── Category facts ───────────────────────────────────────────────────────
    voice = category.get("voice", {})
    category_facts = [
        f"Category: {category.get('slug', '?')}",
        f"Voice: {voice.get('tone', 'peer')}",
        f"Taboos: {voice.get('vocab_taboo', [])}",
    ]
    evidence["category_facts"] = category_facts

    # ── Trigger-specific evidence ────────────────────────────────────────────
    trigger_payload = trigger.get("payload", {})
    trigger_facts = [
        f"Trigger kind: {kind}",
        f"Urgency: {trigger.get('urgency', '?')}",
        f"Suppression key: {trigger.get('suppression_key', '')}",
    ]

    # Resolve digest items for research/compliance/cde/trend triggers
    if kind in ("research_digest", "regulation_change", "category_trend_movement",
                "cde_webinar", "curious_ask_due", "scheduled_recurring"):
        top_item_id = trigger_payload.get("top_item_id")
        digest_items = category.get("digest", [])
        resolved = []
        if top_item_id:
            resolved = [d for d in digest_items if d.get("id") == top_item_id]
        if not resolved:
            # Include up to 2 digest items if no specific one
            resolved = digest_items[:2]
        if resolved:
            for d in resolved[:2]:
                trigger_facts.append(
                    f"Digest: [{d.get('kind','?')}] '{d.get('title','')}' "
                    f"({d.get('source','')}) — {d.get('summary','')}"
                )
            evidence["digest_items"] = resolved[:2]

    # Perf triggers
    if kind in ("perf_dip", "perf_spike"):
        metric = trigger_payload.get("metric", "metric")
        delta = trigger_payload.get("delta_pct")
        baseline = trigger_payload.get("vs_baseline")
        trigger_facts.append(
            f"Perf change: {metric} {_pct(delta)} vs baseline {baseline}"
        )

    # Renewal
    if kind == "renewal_due":
        trigger_facts.append(
            f"Renewal: {trigger_payload.get('days_remaining')} days left, "
            f"₹{trigger_payload.get('renewal_amount','?')} for {trigger_payload.get('plan','?')}"
        )

    # Festival / event
    if kind in ("festival_upcoming", "ipl_match_today"):
        trigger_facts.append(json.dumps(trigger_payload))

    # Review theme
    if kind == "review_theme_emerged":
        theme = trigger_payload.get("theme", "")
        occ = trigger_payload.get("occurrences_30d", "?")
        quote = trigger_payload.get("common_quote", "")
        trigger_facts.append(
            f"Review theme '{theme}': {occ} occurrences, e.g. \"{quote}\""
        )

    # Winback
    if kind == "winback_eligible":
        trigger_facts.append(
            f"Winback: {trigger_payload.get('days_since_expiry')}d since expiry, "
            f"{trigger_payload.get('lapsed_customers_added_since_expiry')} new lapsed customers"
        )

    evidence["trigger_facts"] = trigger_facts

    # ── Customer facts (if customer-facing) ──────────────────────────────────
    if customer:
        cid_facts = []
        cid = customer.get("identity", {})
        rel = customer.get("relationship", {})
        cid_facts.append(f"Customer: {cid.get('name','?')}, lang={cid.get('language_pref','en')}")
        cid_facts.append(f"State: {customer.get('state','?')}")
        cid_facts.append(
            f"Visits: {rel.get('visits_total','?')}, last={rel.get('last_visit','?')}, "
            f"services={rel.get('services_received',[])}"
        )
        prefs = customer.get("preferences", {})
        if prefs:
            cid_facts.append(f"Preferred slot: {prefs.get('preferred_slots','?')}")
        consent = customer.get("consent", {})
        scope_list = consent.get("scope", [])
        cid_facts.append(f"Consent scope: {scope_list}")
        # Recall specifics
        tpay = trigger.get("payload", {})
        if tpay.get("available_slots"):
            cid_facts.append(f"Available slots: {tpay['available_slots']}")
        if tpay.get("service_due"):
            cid_facts.append(f"Service due: {tpay['service_due']}")
        evidence["customer_facts"] = cid_facts

    # ── Seasonal beats & trend signals ───────────────────────────────────────
    beats = category.get("seasonal_beats", [])
    if beats:
        evidence["seasonal_beats"] = beats[:2]

    # ── Conversation history ─────────────────────────────────────────────────
    hist = merchant.get("conversation_history", [])
    if hist:
        evidence["recent_conversation"] = [
            {"from": h["from"], "body": h["body"][:120]} for h in hist[-2:]
        ]

    # ── Review themes ────────────────────────────────────────────────────────
    review_themes = merchant.get("review_themes", [])
    if review_themes:
        evidence["review_themes"] = review_themes[:2]

    return evidence


def _pct(v) -> str:
    if v is None:
        return "?"
    val = float(v)
    sign = "+" if val >= 0 else ""
    return f"{sign}{val*100:.0f}%"
