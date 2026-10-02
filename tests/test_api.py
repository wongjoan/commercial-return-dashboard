"""Access-control and calculation tests. Run:  python -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))
os.environ["GLOCOMP_STATE_PATH"] = os.path.join(tempfile.mkdtemp(), "state.json")
os.environ.pop("VERCEL", None)

from _lib import auth, router, store  # noqa: E402

DIRECTOR, SM_SALES, SM_PRACTICE, MGR_SALES, MGR_CONS = "D0001", "E0078", "E0019", "E0348", "E0308"
SENSITIVE_KEYS = {"base", "base_annual", "variable", "variable_annual", "ote", "ote_annual", "splitPct", "salary_cost",
                  "variable_cost", "total_cost", "expense_cost", "claims", "compensation", "pay_flags", "waves", "onboarding"}


def keys_in(obj):
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= keys_in(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= keys_in(v)
    return out


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.reset_state()
        cls.emp_sales = next(u for u in store.employees() if u["manager_id"] == MGR_SALES)
        cls.emp_other = next(u for u in store.employees() if u["manager_id"] == MGR_CONS)

    def call(self, method, path, uid=None, body=None, cookie=None):
        headers = {"Host": "localhost", "Content-Type": "application/json"}
        if uid:
            headers["Cookie"] = auth.COOKIE + "=" + auth.issue(uid)
        if cookie:
            headers["Cookie"] = cookie
        req = router.Request(method, path, headers, json.dumps(body).encode() if body is not None else b"")
        status, _, payload = router.dispatch(req)
        return status, json.loads(payload)

    # ---------------- authentication
    def test_unauthenticated_rejected(self):
        for p in ("/api/me", "/api/team", "/api/roi", "/api/individuals", "/api/company"):
            self.assertEqual(self.call("GET", p)[0], 401, p)

    def test_tampered_cookie_rejected(self):
        tok = auth.issue(self.emp_sales["id"])
        payload, sig = tok.split(".")
        forged = auth._b64(json.dumps({"sub": DIRECTOR, "exp": 9999999999}).encode()) + "." + sig
        self.assertEqual(self.call("GET", "/api/company", cookie=auth.COOKIE + "=" + forged)[0], 401)

    def test_login_requires_access_code(self):
        s, _ = self.call("POST", "/api/login", body={"user_id": DIRECTOR, "access_code": "wrong"})
        self.assertEqual(s, 401)
        s, _ = self.call("POST", "/api/login", body={"user_id": DIRECTOR, "access_code": "glocomp-demo"})
        self.assertEqual(s, 200)

    def test_cross_origin_post_refused(self):
        req = router.Request("POST", "/api/logout", {"Host": "localhost", "Origin": "https://evil.example", "Content-Type": "application/json"}, b"{}")
        self.assertEqual(router.dispatch(req)[0], 403)

    # ---------------- employee
    def test_employee_sees_own_scorecard_without_pay_data(self):
        s, card = self.call("GET", "/api/me", self.emp_sales["id"])
        self.assertEqual(s, 200)
        self.assertEqual(card["employee"]["id"], self.emp_sales["id"])
        self.assertFalse(SENSITIVE_KEYS & keys_in(card), SENSITIVE_KEYS & keys_in(card))
        self.assertNotIn("case", card)

    def test_employee_cannot_read_others_or_restricted(self):
        e = self.emp_sales["id"]
        self.assertEqual(self.call("GET", "/api/scorecard?id=" + self.emp_other["id"], e)[0], 403)
        self.assertEqual(self.call("GET", "/api/scorecard?id=" + MGR_SALES, e)[0], 403)
        for p in ("/api/team", "/api/roi", "/api/individuals", "/api/company", "/api/hr/cases", "/api/weights", "/api/expenses?id=" + e):
            self.assertEqual(self.call("GET", p, e)[0], 403, p)

    def test_employee_cannot_change_weights_or_use_ai(self):
        e = self.emp_sales["id"]
        self.assertEqual(self.call("POST", "/api/weights/employee", e, {"employee_id": e, "financial_weight": 10, "reason": "I want this"})[0], 403)
        self.assertEqual(self.call("POST", "/api/ai/generate", e, {"employee_id": e})[0], 403)

    # ---------------- manager
    def test_manager_scope_is_own_team(self):
        s, team = self.call("GET", "/api/team", MGR_SALES)
        self.assertEqual(s, 200)
        self.assertTrue(all(p["employee"]["dept"] == "Sales" for p in team["people"]))
        self.assertEqual(self.call("GET", "/api/scorecard?id=" + self.emp_other["id"], MGR_SALES)[0], 403)
        self.assertFalse(SENSITIVE_KEYS & keys_in(team))

    def test_manager_has_no_financial_or_survey_access(self):
        for p in ("/api/roi", "/api/individuals", "/api/company", "/api/departments", "/api/expenses?id=" + self.emp_sales["id"]):
            self.assertEqual(self.call("GET", p, MGR_SALES)[0], 403, p)
        s, cases = self.call("GET", "/api/hr/cases", MGR_SALES)
        self.assertEqual(s, 200)
        self.assertFalse(cases["individual_survey_visible"])
        self.assertFalse(SENSITIVE_KEYS & keys_in(cases), SENSITIVE_KEYS & keys_in(cases))

    def test_manager_weight_band_and_role_template(self):
        e = self.emp_sales["id"]
        rk = "Sales/" + self.emp_sales["title"]
        tpl = store.role_template(rk)
        self.assertEqual(self.call("POST", "/api/weights/role", MGR_SALES, dict(tpl, role_key=rk, reason="try change"))[0], 403)
        s, _ = self.call("POST", "/api/weights/employee", MGR_SALES, {"employee_id": e, "financial_weight": 20, "reason": "too far"})
        self.assertEqual(s, 403)
        before = self.call("GET", "/api/scorecard?id=" + e, MGR_SALES)[1]
        s, res = self.call("POST", "/api/weights/employee", MGR_SALES, {"employee_id": e, "financial_weight": 50, "reason": "Agreed in 1:1 on 2026-10-01"})
        self.assertEqual(s, 200, res)
        after = self.call("GET", "/api/scorecard?id=" + e, MGR_SALES)[1]
        self.assertEqual(after["weightage"]["financial"], 50)
        self.assertTrue(after["weightage"]["individual"])
        self.assertEqual(self.call("GET", "/api/me", e)[1]["weightage"]["financial"], 50)  # employee sees agreed split
        self.assertNotEqual(before["buckets"], after["buckets"])
        self.call("POST", "/api/weights/employee", MGR_SALES, {"employee_id": e, "reset": True})

    def test_invalid_weights_rejected(self):
        e = self.emp_sales["id"]
        bad = {"financial": {"revenue": 50, "ngp": 30, "collections": 10}, "non_financial": {"retention": 60, "win_rate": 40}}
        s, r = self.call("POST", "/api/weights/employee", MGR_SALES, {"employee_id": e, "financial_weight": 60, "kpi_weights": bad, "reason": "bad sum"})
        self.assertEqual(s, 400)
        self.assertIn("add up to 100", r["error"])

    # ---------------- senior manager
    def test_senior_manager_aggregates_only(self):
        s, r = self.call("GET", "/api/roi", SM_SALES)
        self.assertEqual(s, 200)
        self.assertIsNone(r["individuals"])
        self.assertEqual({d["name"] for d in r["departments"]}, {"Sales", "Sales Support"})
        self.assertEqual(self.call("GET", "/api/individuals", SM_SALES)[0], 403)
        self.assertEqual(self.call("GET", "/api/scorecard?id=" + self.emp_other["id"], SM_SALES)[0], 403)
        self.assertEqual(self.call("GET", "/api/scorecard?id=" + self.emp_other["id"], SM_PRACTICE)[0], 200)
        # differencing guard: dept total minus shown team totals must never isolate 1-4 people
        for sm in (SM_SALES, SM_PRACTICE, DIRECTOR):
            r = self.call("GET", "/api/roi", sm)[1]
            for d in r["departments"]:
                shown = sum(t["headcount"] for t in r["teams"] if t["dept"] == d["name"])
                rem = d["headcount"] - shown
                self.assertTrue(shown == 0 or rem == 0 or rem >= 5, (sm, d["name"], rem))

    def test_senior_manager_role_template_scope(self):
        rk_in = "Sales/" + self.emp_sales["title"]
        rk_out = "Consulting/" + self.emp_other["title"]
        tpl = store.role_template(rk_in)
        new = {"financial_weight": 55, "kpi_weights": tpl["kpi_weights"], "reason": "Rebalance FY27"}
        self.assertEqual(self.call("POST", "/api/weights/role", SM_SALES, dict(new, role_key=rk_in))[0], 200)
        self.assertEqual(store.role_template(rk_in)["financial_weight"], 55)
        self.assertEqual(self.call("POST", "/api/weights/role", SM_SALES, dict(new, role_key=rk_out, kpi_weights=store.role_template(rk_out)["kpi_weights"]))[0], 403)
        self.call("POST", "/api/weights/role", SM_SALES, dict(new, role_key=rk_in, financial_weight=60))

    # ---------------- director
    def test_director_full_access(self):
        for p in ("/api/company", "/api/roi", "/api/individuals", "/api/departments", "/api/hr/cases", "/api/expenses?id=" + self.emp_sales["id"]):
            self.assertEqual(self.call("GET", p, DIRECTOR)[0], 200, p)
        r = self.call("GET", "/api/roi", DIRECTOR)[1]
        self.assertEqual(len(r["individuals"]), 64)
        self.assertTrue(self.call("GET", "/api/hr/cases", DIRECTOR)[1]["individual_survey_visible"])

    # ---------------- calculations
    def test_score_is_sum_of_kpi_points(self):
        card = self.call("GET", "/api/me", self.emp_sales["id"])[1]
        fin = sum(k["points_earned"] for k in card["kpis"] if k["bucket"] == "financial")
        nonfin = sum(k["points_earned"] for k in card["kpis"] if k["bucket"] == "non_financial")
        self.assertAlmostEqual(card["buckets"]["financial"]["points"], fin, delta=0.15)
        self.assertAlmostEqual(card["overall"], card["buckets"]["financial"]["points"] + card["buckets"]["non_financial"]["points"], delta=0.15)
        self.assertAlmostEqual(sum(k["points_possible"] for k in card["kpis"]), 100, delta=0.1)

    def test_roi_components_add_up(self):
        r = self.call("GET", "/api/roi", DIRECTOR)[1]
        for row in r["individuals"]:
            parts = row["salary_cost"] + row["variable_cost"] + row["expense_cost"] + row["other_cost"]
            self.assertLessEqual(abs(parts - row["total_cost"]), 3)
            self.assertAlmostEqual(row["roi_pct"], (row["return"] - row["total_cost"]) / row["total_cost"] * 100, delta=0.2)
        self.assertGreater(len({row["expense_cost"] for row in r["individuals"]}), 30)  # cost structures differ

    # ---------------- AI workflow
    def test_ai_suggestions_need_manager_approval(self):
        e = self.emp_sales["id"]
        before = self.call("GET", "/api/me", e)[1]["overall"]
        s, gen = self.call("POST", "/api/ai/generate", MGR_SALES, {"employee_id": e})
        self.assertEqual(s, 200)
        self.assertTrue(gen["suggestions"])
        self.assertTrue(all(x["status"] == "pending_review" for x in gen["suggestions"]))
        self.assertEqual(self.call("GET", "/api/me", e)[1]["overall"], before)  # nothing applied yet
        self.assertEqual(self.call("POST", "/api/ai/apply", MGR_SALES, {"employee_id": e})[0], 400)  # nothing accepted
        new = next((x for x in gen["suggestions"] if x["type"] == "new_kpi"), gen["suggestions"][0])
        s, d = self.call("POST", "/api/ai/decide", MGR_SALES, {"employee_id": e, "suggestion_id": new["id"], "decision": "accept", "suggested_weight": 20})
        self.assertEqual(s, 200)
        self.assertTrue(d["suggestion"]["edited"])
        self.assertEqual(self.call("POST", "/api/ai/apply", MGR_CONS, {"employee_id": e})[0], 403)  # other team's manager
        s, res = self.call("POST", "/api/ai/apply", MGR_SALES, {"employee_id": e})
        self.assertEqual(s, 200, res)
        card = self.call("GET", "/api/me", e)[1]
        k = next(x for x in card["kpis"] if x["key"] == new["kpi"])
        self.assertEqual(k["source"]["type"], "ai_approved")
        self.call("POST", "/api/weights/employee", MGR_SALES, {"employee_id": e, "reset": True})

    # ---------------- HR workflow
    def test_hr_escalation_rules(self):
        cases = self.call("GET", "/api/hr/cases", MGR_SALES)[1]["cases"]
        self.assertTrue(cases, "expected at least one flagged case in the Sales team")
        c = cases[0]
        e = c["employee"]["id"]
        a = c["assessment"]["affected_kpis"][0]
        self.assertTrue(a["possible_causes"] and a["suggested_action"])
        self.assertEqual(self.call("POST", "/api/hr/action", MGR_SALES, {"employee_id": e, "action": "escalate"})[0], 400)  # note required
        self.assertEqual(self.call("POST", "/api/hr/action", MGR_SALES, {"employee_id": e, "action": "record_plan", "action_plan": "Weekly pipeline review"})[0], 200)
        self.assertEqual(self.call("POST", "/api/hr/action", MGR_SALES, {"employee_id": e, "action": "escalate", "note": "No improvement after 2 reviews"})[0], 200)
        # now at senior_manager stage: the manager can no longer move it; the SM can
        self.assertEqual(self.call("POST", "/api/hr/action", MGR_SALES, {"employee_id": e, "action": "escalate", "note": "x"})[0], 403)
        self.assertEqual(self.call("POST", "/api/hr/action", SM_SALES, {"employee_id": e, "action": "escalate", "note": "Exec review needed"})[0], 200)
        self.assertEqual(self.call("POST", "/api/hr/action", SM_SALES, {"employee_id": e, "action": "decide", "decision": "terminate"})[0], 403)
        s, r = self.call("POST", "/api/hr/action", DIRECTOR, {"employee_id": e, "action": "decide", "decision": "continue_support", "note": "Structured plan"})
        self.assertEqual(s, 200)
        self.assertEqual(r["case"]["final_decision"], "continue_support")


if __name__ == "__main__":
    unittest.main()
