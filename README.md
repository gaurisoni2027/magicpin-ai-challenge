# Vera — AI Message Engine for Merchant Growth

> A production-oriented message engine for Vera, Magicpin's AI merchant-growth assistant.

Vera turns merchant context and business triggers into **safe, evidence-backed, actionable conversations**.

The system is designed around one core principle:

**The AI should generate the message — but deterministic logic should control when, why, and whether a message is sent.**

---

## ✨ What This Project Does

Vera receives structured context about:

- Merchant profile and business performance
- Merchant category and communication preferences
- Customer context when applicable
- Growth and performance triggers
- Conversation history

It then decides whether a message should be sent and, when appropriate, generates a concise merchant-facing response containing:

- Message body
- Clear CTA
- Send-as identity
- Suppression key
- Rationale

The engine is built to favor **useful, evidence-backed messages over unnecessary outreach**.

---

## 🧠 Design Philosophy

### Deterministic where it matters. Generative where it helps.

The architecture separates decision-making from language generation:

```text
Incoming Context / Trigger
          │
          ▼
   ┌───────────────┐
   │ Trigger Router│
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ Evidence Builder│
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ LLM Composer  │
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ Deterministic │
   │   Validator   │
   └───────┬───────┘
           │
      ┌────┴────┐
      │         │
     SEND      SAFE
               FALLBACK