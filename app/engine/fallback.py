"""
Deterministic fallback templates.
Used when LLM fails, is unavailable, or validation fails after repair.
Only uses facts that are actually present in the context and trigger payload.
"""
from typing import Optional
import json


def build_fallback(
    trigger: dict,
    merchant: dict,
    category: dict,
    customer: Optional[dict] = None,
) -> dict:
    """
    Returns a safe, fact-grounded fallback composition dict:
    {body, cta, rationale}
    """
    kind = trigger.get("kind", "")
    identity = merchant.get("identity", {})
    name = identity.get("name", "there")
    owner = identity.get("owner_first_name", "")
    slug = category.get("slug", "")

    greeting = f"Dr. {owner}" if slug == "dentists" and owner else (owner or name)

    body, cta, rationale = _choose_template(
        kind, trigger, merchant, category, customer, greeting, owner, name, slug
    )

    return {"body": body, "cta": cta, "rationale": rationale}


def _choose_template(
    kind: str,
    trigger: dict,
    merchant: dict,
    category: dict,
    customer: Optional[dict],
    greeting: str,
    owner: str,
    name: str,
    slug: str,
) -> tuple[str, str, str]:
    sub = merchant.get("subscription", {})
    tpay = trigger.get("payload", {})
    digest = category.get("digest", [])

    # ── Customer-facing triggers (send_as = merchant_on_behalf) ───────────────
    if kind == "recall_due" and customer:
        cust_name = customer.get("identity", {}).get("name", "there")
        service = tpay.get("service_due", "routine checkup").replace("_", " ")
        slots = tpay.get("available_slots", [])
        slot_str = ""
        if slots:
            labels = [s.get("label", "") for s in slots[:2]]
            slot_str = f" Available slots: {' or '.join(labels)}."
        body = (
            f"Hi {cust_name}, this is {name}. Your {service} is due.{slot_str} "
            f"Would you like us to reserve a time for you?"
        )
        return body, "binary_yes_no", f"Customer recall reminder for {service}"

    if kind == "wedding_package_followup" and customer:
        cust_name = customer.get("identity", {}).get("name", "there")
        wedding_date = tpay.get("wedding_date", "your wedding")
        step = tpay.get("next_step_window_open", "bridal prep package").replace("_", " ")
        body = (
            f"Hi {cust_name}, this is {name}! Hope your bridal trial went wonderfully. "
            f"With your wedding on {wedding_date}, now is the ideal time for the {step}. "
            f"Would you like to book your first session?"
        )
        return body, "binary_yes_no", "Bridal package follow-up"

    if kind == "customer_lapsed_hard" and customer:
        cust_name = customer.get("identity", {}).get("name", "there")
        days = tpay.get("days_since_last_visit", 30)
        focus = tpay.get("previous_focus", "fitness").replace("_", " ")
        body = (
            f"Hi {cust_name}, this is {name}. We noticed it has been {days} days since your last visit. "
            f"Ready to restart your {focus} routine? Let us know if you'd like to book a slot this week!"
        )
        return body, "open_ended", "Customer lapsed winback"

    if kind == "trial_followup" and customer:
        cust_name = customer.get("identity", {}).get("name", "there")
        slots = tpay.get("next_session_options", [])
        slot_str = f" Next session option: {slots[0].get('label')}." if slots else ""
        body = (
            f"Hi {cust_name}, this is {name}! How did you enjoy the trial session?{slot_str} "
            f"Would you like us to confirm your spot for the upcoming class?"
        )
        return body, "binary_yes_no", "Trial session follow-up"

    if kind == "chronic_refill_due" and customer:
        cust_name = customer.get("identity", {}).get("name", "there")
        molecules = ", ".join(tpay.get("molecule_list", []))
        mol_str = f" for {molecules}" if molecules else ""
        body = (
            f"Namaste {cust_name}, this is {name}. Your monthly prescription refill{mol_str} is due soon. "
            f"Would you like us to dispatch your delivery to your saved address?"
        )
        return body, "binary_yes_no", "Prescription refill reminder"

    if kind == "appointment_tomorrow" and customer:
        cust_name = customer.get("identity", {}).get("name", "there")
        time_slot = tpay.get("appointment_time", "tomorrow")
        body = (
            f"Hi {cust_name}, this is {name}. Just confirming your appointment scheduled for {time_slot}. "
            f"Please reply 'Confirm' to let us know you'll be there!"
        )
        return body, "binary_confirm_cancel", "Appointment confirmation"

    # ── Research / Compliance / CDE ──────────────────────────────────────────
    if kind in ("research_digest", "regulation_change", "cde_webinar", "cde_opportunity"):
        top_id = tpay.get("top_item_id") or tpay.get("digest_item_id")
        item = next((d for d in digest if d.get("id") == top_id), digest[0] if digest else None)
        if item:
            title = item.get("title", "a new update")
            source = item.get("source", "")
            summary = item.get("summary", "")
            trial_n = item.get("trial_n")
            trial_str = f", n={trial_n}" if trial_n else ""
            src_str = f" ({source}{trial_str})" if source else ""

            if summary:
                body = (
                    f"{greeting}, fresh evidence{src_str}: \"{title}\". "
                    f"{summary} Want me to draft an update for your patients?"
                )
            else:
                body = (
                    f"{greeting}, new {item.get('kind','clinical update')}{src_str}: \"{title}\". "
                    f"Relevant to your {slug} practice. Want me to pull the key points?"
                )
            return body, "open_ended", f"Evidence alert for {slug} practice"

        if kind == "cde_opportunity":
            credits = tpay.get("credits", 2)
            fee = str(tpay.get("fee", "free for members")).replace("_", " ")
            body = (
                f"{greeting}, an upcoming IDA CDE webinar offers {credits} CDE credits ({fee}). "
                f"Would you like me to share the registration details?"
            )
            return body, "binary_yes_no", "CDE credit webinar opportunity"

        body = f"{greeting}, there is a new {kind.replace('_', ' ')} relevant to your {slug} business. Want details?"
        return body, "open_ended", f"Fallback alert for {kind}"

    # ── Performance triggers ──────────────────────────────────────────────────
    if kind in ("perf_dip", "seasonal_perf_dip"):
        metric = tpay.get("metric", "views")
        delta = tpay.get("delta_pct")
        baseline = tpay.get("vs_baseline")
        base_str = f" vs baseline of {baseline}" if baseline else ""
        d_str = f" ({abs(delta*100):.0f}% drop{base_str})" if delta is not None else ""
        note = f" (expected seasonal pattern: {tpay.get('season_note').replace('_',' ')})" if tpay.get("season_note") else ""
        body = f"{greeting}, your {metric}{d_str} dipped this week{note}. Want me to analyze what changed and prepare a recovery promo?"
        return body, "binary_yes_no", f"Performance dip alert for {metric}"

    if kind == "perf_spike":
        metric = tpay.get("metric", "views")
        delta = tpay.get("delta_pct")
        d_str = f" (+{delta*100:.0f}%)" if delta is not None else ""
        driver = f" likely driven by {tpay.get('likely_driver').replace('_',' ')}" if tpay.get("likely_driver") else ""
        body = f"{greeting}, your {metric}{d_str} spiked recently{driver}! Great window to capture new customers — want me to draft a special offer?"
        return body, "binary_yes_no", f"Performance spike alert for {metric}"

    # ── Renewal ───────────────────────────────────────────────────────────────
    if kind == "renewal_due":
        days = tpay.get("days_remaining", sub.get("days_remaining", "?"))
        plan = tpay.get("plan", sub.get("plan", "Pro"))
        amount = tpay.get("renewal_amount")
        amt_str = f" (₹{amount})" if amount else ""
        body = f"{greeting}, your {plan} plan renews in {days} days{amt_str}. Would you like to review renewal options now?"
        return body, "binary_yes_no", "Subscription renewal reminder"

    # ── Review theme ──────────────────────────────────────────────────────────
    if kind == "review_theme_emerged":
        theme = tpay.get("theme", "service")
        occ = tpay.get("occurrences_30d", "several")
        quote = tpay.get("common_quote")
        quote_str = f", e.g. \"{quote}\"" if quote else ""
        body = (
            f"{greeting}, {occ} recent customer reviews mentioned '{theme}'{quote_str}. "
            f"Would you like me to draft a quick response template for your team?"
        )
        return body, "binary_yes_no", f"Review theme alert for {theme}"

    # ── Events / Festivals ───────────────────────────────────────────────────
    if kind == "festival_upcoming":
        fest = tpay.get("festival", "upcoming festival")
        days = tpay.get("days_until")
        days_str = f" in {days} days" if days else " soon"
        body = (
            f"{greeting}, {fest} is coming up{days_str}. "
            f"Would you like me to draft a festive promotional offer for your regulars?"
        )
        return body, "binary_yes_no", f"Festival campaign for {fest}"

    if kind == "ipl_match_today":
        match = tpay.get("match", "today's match")
        venue = tpay.get("venue")
        venue_str = f" at {venue}" if venue else ""
        body = (
            f"{greeting}, {match} is playing today{venue_str}! Match nights see high delivery demand. "
            f"Want me to draft a quick match-night discount offer?"
        )
        return body, "binary_yes_no", f"IPL match campaign for {match}"

    # ── Winback ───────────────────────────────────────────────────────────────
    if kind == "winback_eligible":
        days = tpay.get("days_since_expiry", 30)
        lapsed = tpay.get("lapsed_customers_added_since_expiry", 0)
        lapsed_str = f" We noticed {lapsed} customers have lapsed since then." if lapsed else ""
        body = (
            f"{greeting}, it's been {days} days since your last subscription active period.{lapsed_str} "
            f"Would you like to reconnect with lapsed regulars through a winback campaign?"
        )
        return body, "binary_yes_no", "Merchant winback opportunity"

    # ── Milestones & Planning ─────────────────────────────────────────────────
    if kind == "milestone_reached":
        metric = tpay.get("metric", "reviews").replace("_", " ")
        val_now = tpay.get("value_now", 0)
        target = tpay.get("milestone_value", 0)
        body = (
            f"{greeting}, congratulations! You're at {val_now} {metric}, just shy of the {target} milestone. "
            f"Would you like me to draft a celebration offer to reach {target} this week?"
        )
        return body, "binary_yes_no", f"Milestone alert for {metric}"

    if kind == "active_planning_intent":
        topic = tpay.get("intent_topic", "custom package").replace("_", " ")
        body = (
            f"{greeting}, following up on your request for '{topic}'. "
            f"I have prepared a draft outline and pricing options. Would you like me to share it?"
        )
        return body, "binary_yes_no", f"Active planning follow-up on {topic}"

    # ── Supply Alert & Regulatory ─────────────────────────────────────────────
    if kind == "supply_alert":
        molecule = tpay.get("molecule", "medicine")
        batches = ", ".join(tpay.get("affected_batches", []))
        batch_str = f" (affected batches: {batches})" if batches else ""
        body = (
            f"{greeting}, urgent recall alert for {molecule}{batch_str}. "
            f"Please check your shelf inventory and quarantine any affected units immediately."
        )
        return body, "none", f"Urgent supply recall alert for {molecule}"

    if kind == "category_seasonal":
        season = str(tpay.get("season", "the season")).replace("_", " ")
        trends = tpay.get("trends", [])
        trends_str = f" Key trends: {', '.join(trends[:2])}." if trends else ""
        body = (
            f"{greeting}, seasonal demand shift alert for {season}.{trends_str} "
            f"Would you like me to suggest inventory and offer adjustments?"
        )
        return body, "binary_yes_no", f"Seasonal demand shift for {season}"

    if kind == "gbp_unverified":
        uplift = tpay.get("estimated_uplift_pct", 0.3)
        body = (
            f"{greeting}, your Google Business Profile is currently unverified. "
            f"Verified listings typically see a {uplift*100:.0f}% increase in discovery searches. "
            f"Would you like me to help start the verification process?"
        )
        return body, "binary_yes_no", "Google Business Profile verification reminder"

    if kind == "competitor_opened":
        comp = tpay.get("competitor_name", "A competitor")
        dist = tpay.get("distance_km", 1.5)
        offer = tpay.get("their_offer", "")
        offer_str = f" promoting '{offer}'" if offer else ""
        body = (
            f"{greeting}, {comp} just opened {dist}km away{offer_str}. "
            f"We can launch a targeted loyalty offer to keep your customer retention strong. Want me to draft one?"
        )
        return body, "binary_yes_no", f"Competitor alert: {comp}"

    if kind == "dormant_with_vera":
        days = tpay.get("days_since_last_merchant_message", 30)
        body = (
            f"{greeting}, quick check-in — it has been {days} days since our last chat. "
            f"Is there any new service or promotion at {name} we can highlight for your customers this week?"
        )
        return body, "open_ended", "Re-engagement check-in"

    # ── Generic fallback ──────────────────────────────────────────────────────
    body = (
        f"{greeting}, quick check-in on your {slug or 'local'} business on magicpin. "
        f"Anything I can help you optimize today?"
    )
    return body, "open_ended", f"Generic check-in for kind={kind}"
