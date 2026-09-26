"""
Comprehensive test suite for Vera Bot.
Uses unittest and FastAPI TestClient.
"""
import unittest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app, ctx, conversations, suppression


class TestVeraBot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        # Clear stores before each test to ensure isolation
        ctx._store.clear()
        conversations._convs.clear()
        conversations._merchant_auto_replies.clear()
        suppression._keys.clear()
        suppression._merchant_keys.clear()
        suppression._ended.clear()
        suppression._suppressed_merchants.clear()

    # ─── 1. Healthz & Metadata ────────────────────────────────────────────────

    def test_healthz_endpoint(self):
        resp = self.client.get("/v1/healthz")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("uptime_seconds", data)
        self.assertIn("contexts_loaded", data)
        self.assertEqual(data["contexts_loaded"]["category"], 0)

    def test_metadata_endpoint(self):
        resp = self.client.get("/v1/metadata")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("team_name", data)
        self.assertIn("model", data)
        self.assertIn("approach", data)
        self.assertIn("version", data)

    # ─── 2. Context Push, Idempotency & Version Conflict ───────────────────────

    def test_context_push_success(self):
        payload = {"slug": "dentists", "display_name": "Dentists", "voice": {"tone": "peer_clinical"}}
        resp = self.client.post("/v1/context", json={
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": payload,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("accepted"))
        self.assertIn("ack_id", data)

    def test_context_idempotent_repost(self):
        payload = {"slug": "dentists"}
        # First post v1
        self.client.post("/v1/context", json={
            "scope": "category", "context_id": "dentists", "version": 1, "payload": payload
        })
        # Re-post v1 (idempotent no-op per challenge brief)
        resp2 = self.client.post("/v1/context", json={
            "scope": "category", "context_id": "dentists", "version": 1, "payload": payload
        })
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2.get("accepted"))

    def test_context_higher_version_replaces(self):
        payload_v1 = {"slug": "dentists", "voice": {"tone": "v1"}}
        payload_v2 = {"slug": "dentists", "voice": {"tone": "v2"}}
        self.client.post("/v1/context", json={
            "scope": "category", "context_id": "dentists", "version": 1, "payload": payload_v1
        })
        resp = self.client.post("/v1/context", json={
            "scope": "category", "context_id": "dentists", "version": 2, "payload": payload_v2
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("accepted"))
        stored = ctx.get("category", "dentists")
        self.assertEqual(stored["voice"]["tone"], "v2")

    def test_context_stale_version_conflict(self):
        payload = {"slug": "dentists"}
        self.client.post("/v1/context", json={
            "scope": "category", "context_id": "dentists", "version": 5, "payload": payload
        })
        # Post version 3 (lower than 5)
        resp = self.client.post("/v1/context", json={
            "scope": "category", "context_id": "dentists", "version": 3, "payload": payload
        })
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertFalse(data.get("accepted"))
        self.assertEqual(data.get("reason"), "stale_version")
        self.assertEqual(data.get("current_version"), 5)

    def test_context_invalid_scope(self):
        resp = self.client.post("/v1/context", json={
            "scope": "invalid_scope",
            "context_id": "item1",
            "version": 1,
            "payload": {},
        })
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data.get("accepted"))
        self.assertEqual(data.get("reason"), "invalid_scope")

    # ─── 3. Tick & Compose ────────────────────────────────────────────────────

    def test_tick_with_research_trigger(self):
        # Push category
        cat_payload = {
            "slug": "dentists",
            "voice": {"tone": "peer_clinical", "vocab_taboo": ["guaranteed"]},
            "digest": [
                {
                    "id": "d_fluoride",
                    "title": "3-month fluoride recall cuts caries 38%",
                    "source": "JIDA Oct 2026",
                    "trial_n": 2100,
                    "summary": "Multi-center Indian trial shows 38% reduction.",
                }
            ],
        }
        self.client.post("/v1/context", json={
            "scope": "category", "context_id": "dentists", "version": 1, "payload": cat_payload
        })

        # Push merchant
        merchant_payload = {
            "merchant_id": "m_001_drmeera",
            "category_slug": "dentists",
            "identity": {"name": "Dr. Meera Clinic", "owner_first_name": "Meera", "languages": ["en"]},
            "subscription": {"status": "active", "plan": "Pro"},
        }
        self.client.post("/v1/context", json={
            "scope": "merchant", "context_id": "m_001_drmeera", "version": 1, "payload": merchant_payload
        })

        # Push trigger
        trigger_payload = {
            "id": "trg_research",
            "scope": "merchant",
            "kind": "research_digest",
            "merchant_id": "m_001_drmeera",
            "payload": {"category": "dentists", "top_item_id": "d_fluoride"},
            "urgency": 2,
            "suppression_key": "research:dentists:W17",
            "expires_at": "2026-12-31T00:00:00Z",
        }
        self.client.post("/v1/context", json={
            "scope": "trigger", "context_id": "trg_research", "version": 1, "payload": trigger_payload
        })

        # Tick
        resp = self.client.post("/v1/tick", json={
            "now": "2026-04-26T10:00:00Z",
            "available_triggers": ["trg_research"],
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        actions = data.get("actions", [])
        self.assertEqual(len(actions), 1)

        act = actions[0]
        self.assertEqual(act["merchant_id"], "m_001_drmeera")
        self.assertEqual(act["send_as"], "vera")
        self.assertIn("Dr. Meera", act["body"])
        self.assertIn("38%", act["body"])
        self.assertEqual(act["suppression_key"], "research:dentists:W17")
        self.assertIn("rationale", act)

        # Re-tick: should be suppressed
        resp2 = self.client.post("/v1/tick", json={
            "now": "2026-04-26T10:05:00Z",
            "available_triggers": ["trg_research"],
        })
        self.assertEqual(len(resp2.json().get("actions", [])), 0)

    # ─── 4. Customer-Scoped Trigger & Consent ─────────────────────────────────

    def test_customer_scoped_trigger_with_consent(self):
        cat_payload = {"slug": "dentists", "voice": {"tone": "peer_clinical"}}
        self.client.post("/v1/context", json={
            "scope": "category", "context_id": "dentists", "version": 1, "payload": cat_payload
        })
        m_payload = {
            "merchant_id": "m_001",
            "category_slug": "dentists",
            "identity": {"name": "Meera Dental", "owner_first_name": "Meera"},
            "subscription": {"status": "active"},
        }
        self.client.post("/v1/context", json={
            "scope": "merchant", "context_id": "m_001", "version": 1, "payload": m_payload
        })
        c_payload = {
            "customer_id": "c_priya",
            "merchant_id": "m_001",
            "identity": {"name": "Priya"},
            "consent": {"scope": ["recall_reminders"]},
            "preferences": {"reminder_opt_in": True},
        }
        self.client.post("/v1/context", json={
            "scope": "customer", "context_id": "c_priya", "version": 1, "payload": c_payload
        })
        trg_payload = {
            "id": "trg_recall",
            "scope": "customer",
            "kind": "recall_due",
            "merchant_id": "m_001",
            "customer_id": "c_priya",
            "payload": {
                "service_due": "dental_cleaning",
                "available_slots": [{"iso": "2026-05-01T10:00:00Z", "label": "Fri 10am"}],
            },
            "urgency": 3,
            "suppression_key": "recall:c_priya:2026",
            "expires_at": "2026-12-31T00:00:00Z",
        }
        self.client.post("/v1/context", json={
            "scope": "trigger", "context_id": "trg_recall", "version": 1, "payload": trg_payload
        })

        resp = self.client.post("/v1/tick", json={
            "now": "2026-04-26T10:00:00Z",
            "available_triggers": ["trg_recall"],
        })
        actions = resp.json().get("actions", [])
        self.assertEqual(len(actions), 1)
        act = actions[0]
        self.assertEqual(act["send_as"], "merchant_on_behalf")
        self.assertIn("Priya", act["body"])
        self.assertIn("Meera Dental", act["body"])

    def test_customer_without_consent_is_skipped(self):
        cat_payload = {"slug": "dentists", "voice": {"tone": "peer_clinical"}}
        self.client.post("/v1/context", json={
            "scope": "category", "context_id": "dentists", "version": 1, "payload": cat_payload
        })
        m_payload = {
            "merchant_id": "m_001",
            "category_slug": "dentists",
            "identity": {"name": "Meera Dental"},
            "subscription": {"status": "active"},
        }
        self.client.post("/v1/context", json={
            "scope": "merchant", "context_id": "m_001", "version": 1, "payload": m_payload
        })
        # Customer opted out
        c_payload = {
            "customer_id": "c_optout",
            "merchant_id": "m_001",
            "identity": {"name": "Aman"},
            "consent": {"scope": []},
            "preferences": {"reminder_opt_in": False},
        }
        self.client.post("/v1/context", json={
            "scope": "customer", "context_id": "c_optout", "version": 1, "payload": c_payload
        })
        trg_payload = {
            "id": "trg_recall_optout",
            "scope": "customer",
            "kind": "recall_due",
            "merchant_id": "m_001",
            "customer_id": "c_optout",
            "payload": {"service_due": "dental_cleaning"},
            "urgency": 3,
            "suppression_key": "recall:c_optout:2026",
            "expires_at": "2026-12-31T00:00:00Z",
        }
        self.client.post("/v1/context", json={
            "scope": "trigger", "context_id": "trg_recall_optout", "version": 1, "payload": trg_payload
        })

        resp = self.client.post("/v1/tick", json={
            "now": "2026-04-26T10:00:00Z",
            "available_triggers": ["trg_recall_optout"],
        })
        actions = resp.json().get("actions", [])
        self.assertEqual(len(actions), 0)

    # ─── 5. Reply Handling: Auto-reply, Commitment, Hostility ──────────────────

    def test_reply_auto_reply_progression(self):
        auto_msg = "Thank you for contacting us! Our team will respond shortly."
        mid = "m_test_auto"

        # Turn 1 -> send ping
        r1 = self.client.post("/v1/reply", json={
            "conversation_id": "conv_a_1",
            "merchant_id": mid,
            "from_role": "merchant",
            "message": auto_msg,
            "turn_number": 2,
        })
        self.assertEqual(r1.json().get("action"), "send")

        # Turn 2 -> wait
        r2 = self.client.post("/v1/reply", json={
            "conversation_id": "conv_a_2",
            "merchant_id": mid,
            "from_role": "merchant",
            "message": auto_msg,
            "turn_number": 3,
        })
        self.assertEqual(r2.json().get("action"), "wait")

        # Turn 3 -> end
        r3 = self.client.post("/v1/reply", json={
            "conversation_id": "conv_a_3",
            "merchant_id": mid,
            "from_role": "merchant",
            "message": auto_msg,
            "turn_number": 4,
        })
        self.assertEqual(r3.json().get("action"), "end")

    def test_reply_hostile_stop(self):
        r = self.client.post("/v1/reply", json={
            "conversation_id": "conv_hostile_1",
            "merchant_id": "m_hostile",
            "from_role": "merchant",
            "message": "Stop messaging me. This is useless spam.",
            "turn_number": 2,
        })
        data = r.json()
        self.assertEqual(data.get("action"), "end")
        self.assertTrue(suppression.is_merchant_suppressed("m_hostile"))

    def test_reply_intent_commitment(self):
        r = self.client.post("/v1/reply", json={
            "conversation_id": "conv_intent_1",
            "merchant_id": "m_intent",
            "from_role": "merchant",
            "message": "Ok lets do it. Whats next?",
            "turn_number": 2,
        })
        data = r.json()
        self.assertEqual(data.get("action"), "send")
        body = data.get("body", "").lower()
        qualifying = ["would you", "do you", "can you tell", "what if", "how about"]
        actioning = ["done", "sending", "draft", "here", "confirm", "proceed", "next"]

        self.assertTrue(any(w in body for w in actioning), f"Expected actioning words in: {body}")
        self.assertFalse(any(w in body for w in qualifying), f"Did not expect qualifying words in: {body}")


if __name__ == "__main__":
    unittest.main()
