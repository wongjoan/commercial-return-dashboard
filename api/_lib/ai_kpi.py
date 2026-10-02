"""AI-assisted KPI creation (prototype).

Workflow: historical performance -> AI suggestions -> manager reviews -> manager edits target/weight ->
manager accepts/rejects -> manager approves & applies -> KPI becomes part of the employee's evaluation.

Suggestions are stored with status "pending_review" and have NO effect on scoring until a person with management
authority over the employee explicitly applies them (enforced in router.py + policy.require_manage).

Engine: a deterministic rules-based mock ("mock-rules-v1"), because no LLM API is configured for this prototype.
`generate()` is the seam for a real model: give it the same context dict (KPI history + library + peer medians —
never compensation, expense or survey data) and require the same JSON output shape.
"""
import statistics
import uuid

from . import hr, scoring, store

ENGINE = "mock-rules-v1"
MAX_SUGGESTIONS = 4
NEW_KPI_WEIGHT = 15


def _att(direction, target, actual):
    return scoring._attainment(direction, target, actual) * 100


def _round_target(v, unit):
    if unit == "RM":
        return round(v, -3)
    if unit == "count":
        return max(1, round(v))
    if unit in ("x", "days"):
        return round(v, 1)
    return round(min(v, 100.0), 1)


def _peer_actuals(u, key):
    rec_all = store.seed()["kpi_records"]
    return [rec_all[p["id"]][key]["actual"] for p in store.employees()
            if p.get("manager_id") == u.get("manager_id") and p["id"] != u["id"] and p["dept"] == u["dept"]]


def build_context(u, card):
    """Everything the engine is allowed to see. Deliberately excludes pay, cost, expenses and survey answers."""
    rec = store.seed()["kpi_records"][u["id"]]
    lib = scoring.library(u["dept"])
    in_use = {k["key"] for k in card["kpis"]}
    return {
        "role": u["title"], "department": u["dept"], "period": card["period"],
        "current_kpis": card["kpis"], "weightage": card["weightage"],
        "candidates": {k: dict(lib[k], history=rec[k]["history"] + [{"period": card["period"], "target": rec[k]["target"], "actual": rec[k]["actual"]}],
                               peer_actuals=_peer_actuals(u, k))
                       for k in lib if k not in in_use},
    }


def generate(u, card):
    ctx = build_context(u, card)
    out = []

    def add(**kw):
        kw.update(id="S-" + uuid.uuid4().hex[:8], status="pending_review", engine=ENGINE, generated_at=store.now_iso(),
                  edited=False, decided_by=None)
        out.append(kw)

    # 1) Re-target existing KPIs whose history shows the target is mis-calibrated.
    for k in ctx["current_kpis"]:
        seq = [h["attainment"] for h in k["history"]] + [k["attainment_pct"]]
        actuals = [h["actual"] for h in k["history"]] + [k["actual"]]
        if all(a >= 105 for a in seq):
            new_t = _round_target(statistics.mean(actuals) * (1.02 if k["direction"] == "higher" else 0.98), k["unit"])
            add(type="retarget", kpi=k["key"], name=k["name"], bucket=k["bucket"], unit=k["unit"], direction=k["direction"],
                current_target=k["target"], suggested_target=new_t, current_weight=k["weight_in_bucket"], suggested_weight=k["weight_in_bucket"],
                confidence="high",
                reason="Target has been exceeded in all 4 quarters (%s), so it no longer differentiates performance. Suggest a stretch target near the recent average." % ", ".join("%.0f%%" % a for a in seq),
                evidence=["4-quarter attainment: " + ", ".join("%.0f%%" % a for a in seq), "Average actual: %s" % hr._fmt(statistics.mean(actuals), k["unit"])])
        elif all(a < 85 for a in seq):
            pm, n = hr._peer_median(u, k["key"])
            if pm is not None and pm < 85:
                new_t = _round_target(k["target"] * (pm / 100) * 1.05 if k["direction"] == "higher" else k["target"] / ((pm / 100) * 1.05), k["unit"])
                add(type="retarget", kpi=k["key"], name=k["name"], bucket=k["bucket"], unit=k["unit"], direction=k["direction"],
                    current_target=k["target"], suggested_target=new_t, current_weight=k["weight_in_bucket"], suggested_weight=k["weight_in_bucket"],
                    confidence="medium",
                    reason="Missed in every quarter AND the team median is also low (%.0f%%, n=%d) — the target looks unrealistic for current conditions rather than an individual issue. Manager should confirm before lowering." % (pm, n),
                    evidence=["4-quarter attainment: " + ", ".join("%.0f%%" % a for a in seq), "Peer median attainment: %.0f%%" % pm])

    # 2) Re-weight towards the KPI causing the biggest, worsening gap.
    worst = sorted(ctx["current_kpis"], key=lambda k: -k["points_lost"])
    if worst and worst[0]["points_lost"] >= 3:
        k = worst[0]
        siblings = [x for x in ctx["current_kpis"] if x["bucket"] == k["bucket"]]
        if len(siblings) > 1 and k["weight_in_bucket"] <= 60 and not any(s["kpi"] == k["key"] for s in out):
            seq = [h["attainment"] for h in k["history"]] + [k["attainment_pct"]]
            add(type="reweight", kpi=k["key"], name=k["name"], bucket=k["bucket"], unit=k["unit"], direction=k["direction"],
                current_target=k["target"], suggested_target=k["target"], current_weight=k["weight_in_bucket"],
                suggested_weight=min(70, k["weight_in_bucket"] + 10), confidence="medium",
                reason="Largest source of lost points this quarter (%.1f pts). Increasing its weight focuses the improvement plan on it; other %s KPIs are rebalanced proportionally." % (k["points_lost"], scoring.BUCKET_LABEL[k["bucket"]].lower()),
                evidence=["4-quarter attainment: " + ", ".join("%.0f%%" % a for a in seq)])

    # 3) Introduce a relevant KPI that is tracked in source systems but not yet part of the evaluation.
    gap_keys = [k["key"] for k in worst[:2]]
    ranked = sorted(ctx["candidates"].items(), key=lambda kv: (0 if any(kv[0] in hr.LEADING.get(g, []) for g in gap_keys) else 1, kv[0]))
    for key, c in ranked:
        if len(out) >= MAX_SUGGESTIONS:
            break
        hist = c["history"]
        own_mean = statistics.mean(h["actual"] for h in hist)
        peers = c["peer_actuals"]
        peer_med = statistics.median(peers) if len(peers) >= 3 else None
        base = own_mean if peer_med is None else (max(own_mean, peer_med) if c["direction"] == "higher" else min(own_mean, peer_med))
        tgt = _round_target(base * (1.03 if c["direction"] == "higher" else 0.97), c["unit"])
        leading_for = [scoring.library(u["dept"])[g]["name"] for g in gap_keys if key in hr.LEADING.get(g, [])]
        why = ("It is a leading indicator for %s, which is currently below target." % " and ".join(leading_for)) if leading_for else \
              "It is relevant to the %s role and already tracked in source systems." % u["title"]
        add(type="new_kpi", kpi=key, name=c["name"], bucket=c["bucket"], unit=c["unit"], direction=c["direction"],
            current_target=None, suggested_target=tgt, current_weight=0, suggested_weight=NEW_KPI_WEIGHT,
            confidence="medium" if peer_med is not None else "low",
            reason="Based on historical performance and the employee's role: %s Own 4-quarter average %s%s; suggested target sets a modest stretch." % (
                why, hr._fmt(own_mean, c["unit"]), (", team median %s" % hr._fmt(peer_med, c["unit"])) if peer_med is not None else ""),
            evidence=["History: " + ", ".join("%s %s" % (h["period"], hr._fmt(h["actual"], c["unit"])) for h in hist), "Formula: " + c["formula"]])
    return out[:MAX_SUGGESTIONS]


def _rebalance(weights, key, new_w):
    """Set weights[key] = new_w and scale the others so the bucket still sums to 100 (integers)."""
    others = {k: w for k, w in weights.items() if k != key}
    remaining = 100 - new_w
    tot = sum(others.values())
    out = {}
    if others:
        scaled = {k: (w / tot * remaining if tot else remaining / len(others)) for k, w in others.items()}
        floored = {k: int(v) for k, v in scaled.items()}
        short = remaining - sum(floored.values())
        for k in sorted(scaled, key=lambda k: -(scaled[k] - floored[k]))[:short]:
            floored[k] += 1
        out.update(floored)
    out[key] = new_w
    return out


def apply_accepted(actor, u, items):
    cfg = scoring.effective_config(u)
    ov = store.employee_override(u["id"])
    kw = {b: dict(v) for b, v in cfg["kpi_weights"].items()}
    targets = dict(cfg["targets"])
    sources = dict(cfg["kpi_sources"])
    applied = []
    for s in items:
        if s["status"] != "accepted":
            continue
        b, k = s["bucket"], s["kpi"]
        if s["type"] in ("new_kpi", "reweight"):
            kw[b] = _rebalance(kw[b], k, int(s["suggested_weight"]))
        if s["type"] in ("new_kpi", "retarget"):
            targets[k] = s["suggested_target"]
        sources[k] = {"type": "ai_approved", "suggestion_id": s["id"],
                      "label": "AI-suggested (%s), %s by %s on %s" % (s["type"].replace("_", " "), "edited and approved" if s["edited"] else "approved", actor["name"], store.now_iso()[:10])}
        s["status"] = "applied"
        applied.append(s["id"])
    if not applied:
        raise ValueError("No accepted suggestions to apply")
    scoring.validate_config(u["dept"], cfg["financial_weight"], kw)
    ov.update(financial_weight=cfg["financial_weight"], kpi_weights=kw, targets=targets, kpi_sources=sources,
              agreed_by=actor["name"], agreed_at=store.now_iso(), reason="Applied AI-assisted KPI suggestions after manager review")
    store.set_employee_override(u["id"], ov)
    return applied
