"""LLM path of the AI KPI assistant, tested with a fake model (no network, no API key needed).
Run:  python -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))
os.environ.setdefault("GLOCOMP_STATE_PATH", os.path.join(tempfile.mkdtemp(), "state.json"))
os.environ.pop("VERCEL", None)

from _lib import ai_kpi, auth, llm, router, scoring, store  # noqa: E402

MGR_SALES = "E0348"
LLM_ENV = {"LLM_PROVIDER": "anthropic", "LLM_API_KEY": "test-key", "LLM_MODEL": "test-model"}


class AiLlm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.reset_state()
        cls.u = next(u for u in store.employees() if u["manager_id"] == MGR_SALES)

    def setUp(self):
        self.card = scoring.scorecard(self.u)
        self.cur = {k["key"]: k for k in self.card["kpis"]}
        self.cand = next(iter(ai_kpi.build_context(self.u, self.card)["candidates"]))

    def fake(self, items):
        return mock.patch.object(llm, "complete", return_value=json.dumps({"suggestions": items}))

    def test_not_configured_uses_rules(self):
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            items, meta = ai_kpi.generate_with_meta(self.u, self.card)
        self.assertEqual(meta, {"engine": "mock-rules-v1", "fallback": None})
        self.assertTrue(items)

    def test_valid_llm_output_is_kept_and_grounded(self):
        k = self.card["kpis"][0]
        hist = ai_kpi.build_context(self.u, self.card)["candidates"][self.cand]["history"]
        own = sum(h["actual"] for h in hist) / len(hist)
        raw = [
            {"type": "retarget", "kpi": k["key"], "suggested_target": k["target"] * 1.1, "confidence": "high",
             "reason": "Beaten every quarter.", "evidence": ["x"], "bucket": "HACKED", "unit": "HACKED"},
            {"type": "new_kpi", "kpi": self.cand, "suggested_target": own * 1.05, "suggested_weight": 20,
             "confidence": "medium", "reason": "Leading indicator."},
        ]
        with mock.patch.dict(os.environ, LLM_ENV), self.fake(raw):
            items, meta = ai_kpi.generate_with_meta(self.u, self.card)
        self.assertEqual(meta, {"engine": "anthropic:test-model", "fallback": None})
        self.assertEqual([s["kpi"] for s in items], [k["key"], self.cand])
        r = items[0]
        self.assertEqual((r["bucket"], r["unit"], r["current_target"]), (k["bucket"], k["unit"], k["target"]))  # from data, not model
        self.assertTrue(r["evidence"][0].startswith("4-quarter attainment"))  # server-computed fact first
        self.assertTrue(all(s["status"] == "pending_review" for s in items))
        self.assertEqual(items[1]["suggested_weight"], 20)

    def test_invalid_llm_items_are_dropped(self):
        k = self.card["kpis"][0]
        raw = [
            {"type": "retarget", "kpi": "made_up_kpi", "suggested_target": 1, "reason": "x"},          # unknown KPI
            {"type": "retarget", "kpi": k["key"], "suggested_target": k["target"] * 10, "reason": "x"},  # implausible
            {"type": "reweight", "kpi": k["key"], "suggested_weight": 99, "reason": "x"},               # out of range
            {"type": "new_kpi", "kpi": k["key"], "suggested_target": 5, "reason": "x"},                 # already in use
            {"type": "delete_kpi", "kpi": k["key"], "reason": "x"},                                     # unknown type
            {"type": "retarget", "kpi": k["key"], "suggested_target": k["target"] * 1.1, "reason": ""},  # no reason
        ]
        with mock.patch.dict(os.environ, LLM_ENV), self.fake(raw):
            items, meta = ai_kpi.generate_with_meta(self.u, self.card)
        self.assertEqual(meta["engine"], "mock-rules-v1")  # nothing valid -> rules fallback
        self.assertEqual(meta["fallback"], "LLM returned no valid suggestions")
        self.assertTrue(all(s["engine"] == "mock-rules-v1 (fallback)" for s in items))

    def test_llm_error_or_garbage_falls_back(self):
        for side in (llm.LLMError("HTTP 500 from LLM provider"), None):
            p = mock.patch.object(llm, "complete", side_effect=side) if side else \
                mock.patch.object(llm, "complete", return_value="Sure! Here are some ideas...")
            with mock.patch.dict(os.environ, LLM_ENV), p:
                items, meta = ai_kpi.generate_with_meta(self.u, self.card)
            self.assertEqual(meta["engine"], "mock-rules-v1")
            self.assertTrue(meta["fallback"])
            self.assertTrue(items)

    def test_prompt_contains_no_identity_or_pay(self):
        with mock.patch.dict(os.environ, LLM_ENV), mock.patch.object(llm, "complete", return_value='{"suggestions": []}') as m:
            ai_kpi.generate_with_meta(self.u, self.card)
        sent = m.call_args[0][1]
        comp = store.seed()["compensation"][self.u["id"]]
        for bad in (self.u["name"], self.u["id"], "salary", "commission", "expense", "pulse", "survey"):
            self.assertNotIn(bad.lower(), sent.lower(), bad)
        self.assertNotIn(str(comp["base_annual"]), sent)

    def test_api_reports_engine_and_still_needs_approval(self):
        e = self.u["id"]
        hdr = {"Host": "localhost", "Content-Type": "application/json", "Cookie": auth.COOKIE + "=" + auth.issue(MGR_SALES)}
        k = self.card["kpis"][0]
        raw = [{"type": "retarget", "kpi": k["key"], "suggested_target": k["target"] * 1.1, "reason": "Calibrate."}]
        with mock.patch.dict(os.environ, LLM_ENV), self.fake(raw):
            st, _, body = router.dispatch(router.Request("POST", "/api/ai/generate", hdr, json.dumps({"employee_id": e}).encode()))
        res = json.loads(body)
        self.assertEqual(st, 200)
        self.assertEqual(res["engine"], "anthropic:test-model")
        self.assertIsNone(res["fallback"])
        self.assertEqual(scoring.scorecard(self.u)["overall"], self.card["overall"])  # nothing applied


if __name__ == "__main__":
    unittest.main()
