"""HR intervention: performance gap -> affected KPI -> possible cause/context -> suggested action ->
manager review -> escalation (Manager -> Senior Manager -> Executive/HR review).

Nothing here labels a person "underperforming" on its own: every flag is tied to a named KPI, its gap, the evidence
for a likely cause, and a concrete next step. Stage changes are always manual actions by an authorised person.
"""
import statistics

from . import policy, scoring, store

STAGES = [
    {"id": "flagged", "label": "Flagged — awaiting manager review", "owner_level": "manager", "kind": "warning"},
    {"id": "manager_review", "label": "Manager action plan in place", "owner_level": "manager", "kind": "warning"},
    {"id": "senior_manager", "label": "Escalated to senior manager", "owner_level": "senior_manager", "kind": "critical"},
    {"id": "exec_hr_review", "label": "Executive / HR review", "owner_level": "director", "kind": "critical"},
    {"id": "closed", "label": "Closed", "owner_level": None, "kind": "good"},
]
STAGE_IDS = [s["id"] for s in STAGES]
FINAL_DECISIONS = {"resolved": "Resolved — gap closed", "continue_support": "Continue structured support plan",
                   "role_change": "Redeploy / role change", "terminate": "End employment (HR process)"}

# Leading indicators that commonly explain a gap in a lagging KPI.
LEADING = {
    "revenue": ["pipeline_coverage", "win_rate"], "ngp": ["win_rate", "pipeline_coverage"], "collections": ["forecast_accuracy"],
    "retention": ["forecast_accuracy"], "win_rate": ["pipeline_coverage"],
    "renewal_value": ["renewal_completion", "first_time_right"], "renewal_completion": ["turnaround"], "order_accuracy": ["first_time_right"],
    "attributed_ngp": ["deal_support_win", "enablement_sessions"], "deal_support_win": ["enablement_sessions"], "sla_compliance": ["roadmap_on_time"],
    "chargeable_revenue": ["utilisation"], "utilisation": ["scoping_turnaround"], "scoping_accuracy": ["delivery_quality"],
    "project_margin": ["change_request_margin", "on_time"], "dso": ["csat"], "csat": ["defect_escape", "on_time"], "on_time": ["defect_escape"],
}

PLAYBOOK = {
    "revenue": "Weekly pipeline review with the manager; agree 3 target accounts to accelerate; pair on late-stage deals.",
    "ngp": "Review discounting on recent deals against pricing guardrails; involve Product/Consulting earlier to protect margin.",
    "collections": "Agree a collections cadence with Finance for overdue invoices; confirm billing milestones at deal close.",
    "retention": "Run account-health reviews on at-risk customers; schedule QBRs for the top 5 accounts by value.",
    "win_rate": "Deal-qualification coaching (e.g. MEDDICC) and loss reviews on the last 5 lost deals.",
    "renewal_value": "Build a 90-day renewal calendar and start renewal admin 60 days before expiry.",
    "order_accuracy": "Introduce a 4-eyes check on orders above RM50K; refresh order-entry checklist training.",
    "renewal_completion": "Daily renewals stand-up and earlier hand-off from Sales on expiring contracts.",
    "turnaround": "Template the top 10 quote types; review queue prioritisation with the team lead.",
    "attributed_ngp": "Prioritise deal-support time on the highest-NGP opportunities; agree engagement rules with Sales.",
    "deal_support_win": "Review supported-deal losses with Sales; refresh competitive positioning material.",
    "sla_compliance": "Triage requests by SLA tier; agree a request intake channel with Sales.",
    "chargeable_revenue": "Resource-manager review of allocation; move to billable work before internal projects.",
    "utilisation": "Review allocation and bench time with the resourcing lead; reduce non-billable admin.",
    "scoping_accuracy": "Peer review of scopes above 40 days; compare estimates to actuals on the last 3 projects.",
    "project_margin": "Change-request discipline: log and price scope changes; weekly burn vs. budget review.",
    "dso": "Confirm milestone acceptance promptly; joint follow-up with Finance on invoices over 45 days.",
    "csat": "Mid-project client check-ins; act on survey verbatims within two weeks.",
    "on_time": "Weekly milestone risk review; escalate dependencies earlier.",
}


def _pattern(kpi):
    hist = [h["attainment"] for h in kpi["history"]]
    cur = kpi["attainment_pct"]
    seq = hist + [cur]
    if all(a < 85 for a in seq):
        return "sustained", "Below target in every quarter tracked (%s)" % ", ".join("%.0f%%" % a for a in seq)
    if all(seq[i] > seq[i + 1] for i in range(len(seq) - 1)):
        return "declining", "Steady decline over 4 quarters (%s)" % " → ".join("%.0f%%" % a for a in seq)
    if statistics.mean(hist) >= 90 and cur < 80:
        return "recent_drop", "Recent drop: averaged %.0f%% before this quarter, %.0f%% now" % (statistics.mean(hist), cur)
    return "mixed", "Uneven over recent quarters (%s)" % ", ".join("%.0f%%" % a for a in seq)


def _peer_median(u, key):
    peers = [p for p in policy.direct_reports(u.get("manager_id")) if p["id"] != u["id"] and p["dept"] == u["dept"]]
    vals = []
    rec_all = store.seed()["kpi_records"]
    lib = scoring.library(u["dept"])
    for p in peers:
        r = rec_all[p["id"]].get(key)
        if r:
            vals.append(scoring._attainment(lib[key]["direction"], r["target"], r["actual"]) * 100)
    return (statistics.median(vals), len(vals)) if len(vals) >= 3 else (None, len(vals))


def _leading(u, key):
    out = []
    rec = store.seed()["kpi_records"][u["id"]]
    lib = scoring.library(u["dept"])
    for lk in LEADING.get(key, []):
        if lk in rec:
            att = scoring._attainment(lib[lk]["direction"], rec[lk]["target"], rec[lk]["actual"]) * 100
            if att < 85:
                out.append("%s is also below target (%s vs. %s target) — a likely upstream cause"
                           % (lib[lk]["name"], _fmt(rec[lk]["actual"], lib[lk]["unit"]), _fmt(rec[lk]["target"], lib[lk]["unit"])))
    return out


def _fmt(v, unit):
    if unit == "RM":
        return "RM%sK" % format(round(v / 1000), ",")
    if unit == "%":
        return "%.1f%%" % v
    if unit == "days":
        return "%.1f days" % v
    if unit == "x":
        return "%.1fx" % v
    return str(v)


def _mentions_workload(wave):
    # Data-quality note: in the supplied synthetic dataset ~87% of numeric workload scores are exactly 1.0 (looks
    # clipped), while many comments raise workload — so the free-text comment is used as a second signal.
    return "workload" in (wave.get("comment") or "").lower()


def survey_context(uid):
    """Individual survey-derived signals. HR-confidential: only returned to authorised levels (see policy)."""
    ctx = store.seed()["hr_context"]
    waves = ctx["pulse"].get(uid, [])
    u = store.user(uid)
    out = []
    if waves:
        last = waves[-1]
        if last["workload"] >= 4.0 or _mentions_workload(last):
            out.append({"theme": "Workload", "detail": "Latest pulse: workload pressure %.1f/5 (higher = worse); comment: \"%s\"" % (last["workload"], last["comment"])})
        if last["manager_support"] <= 2.5:
            out.append({"theme": "Manager support", "detail": "Latest pulse: manager support %.1f/5" % last["manager_support"]})
        if last["career_growth"] <= 2.5:
            out.append({"theme": "Career growth", "detail": "Latest pulse: career growth %.1f/5" % last["career_growth"]})
        if len(waves) >= 2 and waves[0]["engagement"] - last["engagement"] >= 0.5:
            out.append({"theme": "Engagement drop", "detail": "Engagement fell from %.1f to %.1f across pulse waves" % (waves[0]["engagement"], last["engagement"])})
    ob = ctx["onboarding"].get(uid)
    if u and u.get("hr_link") and u["hr_link"]["tenure_months"] <= 9 and ob and ob["role_clarity"] < 3.2:
        out.append({"theme": "New joiner — role clarity", "detail": "%d months tenure; onboarding role clarity %.1f/5" % (u["hr_link"]["tenure_months"], ob["role_clarity"])})
    return {"signals": out, "waves": waves, "onboarding": ob}


def team_themes(uids):
    """Aggregated pulse themes for a group — suppressed below policy.MIN_GROUP respondents."""
    pulse = store.seed()["hr_context"]["pulse"]
    latest = [pulse[u][-1] for u in uids if pulse.get(u)]
    if len(latest) < policy.MIN_GROUP:
        return {"suppressed": True, "respondents": len(latest), "min_group": policy.MIN_GROUP}
    avg = {k: round(statistics.mean(w[k] for w in latest), 2) for k in ("engagement", "manager_support", "workload", "career_growth", "belonging")}
    return {"suppressed": False, "respondents": len(latest), "averages": avg,
            "high_workload_share": round(sum(1 for w in latest if w["workload"] >= 4 or _mentions_workload(w)) / len(latest) * 100)}


def related_actions(u, signals):
    themes = {s["theme"] for s in signals}
    out = []
    for a in store.seed()["hr_context"]["actions"]:
        scope_ok = a["scope"] in ("All", u["hr_link"]["source_division"] if u.get("hr_link") else None)
        theme_ok = ("Workload" in themes and "workload" in (a["issue"] or "").lower()) or \
                   ("Career growth" in themes and "career" in (a["theme"] or "").lower()) or \
                   ("New joiner — role clarity" in themes and "role clarity" in (a["issue"] or "").lower())
        if scope_ok and theme_ok:
            out.append({"id": a["action_id"], "action": a["action"], "owner": a["owner"], "status": a["status"],
                        "success_measure": a.get("success_measure")})
    return out


def assess(u, card, include_individual_survey):
    """Gap analysis for one employee. Returns None if nothing needs attention."""
    flags = []
    if card["overall"] < 70:
        flags.append({"id": "overall", "label": "Overall score below 70%", "kind": "critical"})
    gaps = [k for k in card["kpis"] if k["attainment_pct"] < 85 and k["points_possible"] >= 8]
    if gaps and 70 <= card["overall"] < 80:
        flags.append({"id": "kpi_gap", "label": "Material gap on a heavily weighted KPI", "kind": "warning"})
    trend = [t["overall"] for t in card["trend"]]
    if all(trend[i] > trend[i + 1] for i in range(len(trend) - 1)) and trend[0] - trend[-1] >= 8:
        flags.append({"id": "declining", "label": "Score declining for 4 quarters", "kind": "warning"})
    if not flags:
        return None

    quarters_below = 0
    for t in reversed(card["trend"]):
        if t["overall"] < 75:
            quarters_below += 1
        else:
            break
    suggested = "flagged" if quarters_below <= 1 else ("senior_manager" if quarters_below == 2 else "exec_hr_review")

    survey = survey_context(u["id"]) if include_individual_survey else None
    affected = []
    for k in sorted(gaps or [k for k in card["kpis"] if k["attainment_pct"] < 95], key=lambda x: -x["points_lost"])[:3]:
        pattern, pattern_text = _pattern(k)
        causes = [pattern_text]
        pm, n = _peer_median(u, k["key"])
        if pm is not None:
            if pm < 85:
                causes.append("Team-wide: peers' median attainment is also low (%.0f%%, n=%d) — check whether the target or market conditions are the issue before treating it as individual" % (pm, n))
            else:
                causes.append("Individual: peers' median attainment is %.0f%% (n=%d), so the gap is specific to this person's portfolio or approach" % (pm, n))
        causes += _leading(u, k["key"])
        affected.append({
            "kpi": k["key"], "name": k["name"], "bucket": k["bucket"], "target": k["target"], "actual": k["actual"], "unit": k["unit"],
            "attainment_pct": k["attainment_pct"], "points_lost": k["points_lost"], "pattern": pattern, "possible_causes": causes,
            "suggested_action": PLAYBOOK.get(k["key"], "Agree a specific improvement goal for this KPI and review fortnightly."),
        })
    actions = [a["suggested_action"] for a in affected]
    if survey:
        for s in survey["signals"]:
            if s["theme"] == "Workload":
                actions.append("Survey shows high workload — review allocation/territory before setting stretch targets.")
            elif s["theme"] == "Manager support":
                actions.append("Survey shows low manager support — senior manager to sit in on the next 1:1 or assign a mentor.")
            elif s["theme"] == "Career growth":
                actions.append("Survey shows low career-growth score — include a development goal in the improvement plan.")
            elif s["theme"] == "New joiner — role clarity":
                actions.append("Recent joiner with low role clarity — restate the agreed KPI targets and run a 30-day expectation check-in.")
    return {
        "flags": flags, "gap_points": round(100 - card["overall"], 1), "affected_kpis": affected,
        "quarters_below_75": quarters_below, "suggested_stage": suggested, "recommended_actions": actions,
        "survey": survey, "related_org_actions": related_actions(u, survey["signals"]) if survey else [],
    }


def ensure_case(u, assessment):
    c = store.hr_case(u["id"])
    if not c:
        c = {"stage": "flagged", "suggested_stage": assessment["suggested_stage"], "owner": "", "notes": "",
             "action_plan": "", "review_date": "", "final_decision": None, "history": [], "opened_at": store.now_iso()}
        store.set_hr_case(u["id"], c)
    return c


def stage_meta(sid):
    return next(s for s in STAGES if s["id"] == sid)


def allowed_actions(actor, target, case):
    """What this actor may do to this case right now — computed server-side and re-checked on POST."""
    if not policy.manages(actor, target["id"]):
        return []
    lvl, st = actor["level"], case["stage"]
    acts = []
    if st == "closed":
        return ["reopen"] if lvl in ("senior_manager", "director") else []
    if st in ("flagged", "manager_review"):
        acts += ["record_plan", "escalate", "resolve"]
    if st == "senior_manager" and lvl in ("senior_manager", "director"):
        acts += ["record_plan", "escalate", "return_to_manager", "resolve"]
    if st == "exec_hr_review" and lvl == "director":
        acts += ["record_plan", "decide", "return_to_manager"]
    if lvl == "director":
        acts.append("set_owner")
    return acts


def apply_action(actor, target, case, action, body):
    if action not in allowed_actions(actor, target, case):
        raise policy.Forbidden("Action '%s' is not available to you at stage '%s'" % (action, case["stage"]))
    note = (body.get("note") or "").strip()[:1000]
    st = case["stage"]
    if action == "record_plan":
        plan = (body.get("action_plan") or "").strip()[:1500]
        if not plan:
            raise ValueError("An action plan is required")
        case["action_plan"] = plan
        case["review_date"] = (body.get("review_date") or "")[:10]
        if st == "flagged":
            case["stage"] = "manager_review"
    elif action == "escalate":
        if not note:
            raise ValueError("Escalation needs a note explaining why")
        case["stage"] = {"flagged": "senior_manager", "manager_review": "senior_manager", "senior_manager": "exec_hr_review"}[st]
    elif action == "return_to_manager":
        case["stage"] = "manager_review"
    elif action == "resolve":
        case["stage"], case["final_decision"] = "closed", "resolved"
    elif action == "decide":
        d = body.get("decision")
        if d not in FINAL_DECISIONS:
            raise ValueError("Unknown decision")
        case["final_decision"] = d
        if d != "continue_support":
            case["stage"] = "closed"
    elif action == "reopen":
        case["stage"], case["final_decision"] = "flagged", None
    elif action == "set_owner":
        case["owner"] = (body.get("owner") or "").strip()[:120]
    if note:
        case["notes"] = note
    case["history"].append({"at": store.now_iso(), "by": actor["id"], "by_name": actor["name"], "action": action,
                            "from": st, "to": case["stage"], "note": note})
    store.set_hr_case(target["id"], case)
    store.audit(actor, "hr_case." + action, target["id"], {"from": st, "to": case["stage"]})
    return case


def pay_flags(uid):
    """Compensation-derived flags from the original prototype (pay equity, income volatility). Director-only."""
    s = store.seed()
    comp = s["compensation"][uid]
    u = store.user(uid)
    peers = [s["compensation"][p["id"]]["base_annual"] for p in store.employees() if p["title"] == u["title"]]
    out = []
    if len(peers) >= 2:
        med = statistics.median(peers)
        below = (med - comp["base_annual"]) / med
        if below > 0.10:
            out.append({"id": "pay-equity", "label": "Pay equity", "detail": "Base %.0f%% below the %s median (RM%s)" % (below * 100, u["title"], format(round(med), ","))})
    if comp["pay_structure"] == "Commission":
        split = comp["variable_annual"] / comp["ote_annual"]
        if split > 0.35:
            out.append({"id": "income-volatility", "label": "Income volatility", "detail": "%.0f%% of OTE is variable pay — above the 35%% watch line" % (split * 100)})
    return out
