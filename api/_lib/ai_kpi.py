"""AI-assisted KPI creation.

Workflow: historical performance -> AI suggestions -> manager reviews -> manager edits target/weight ->
manager accepts/rejects -> manager approves & applies -> KPI becomes part of the employee's evaluation.

Suggestions are stored with status "pending_review" and have NO effect on scoring until a person with management
authority over the employee explicitly applies them (enforced in router.py + policy.require_manage).

Engines:
  * LLM (when an API key is configured — see llm.py). The model receives ONLY KPI history, the role's KPI library,
    peer medians and leading-indicator links: no names, no pay, cost, expense or survey data. Every suggestion it
    returns is validated against the real data (KPI must exist, numbers in range, one suggestion per KPI) and
    anything that fails is dropped. Bucket, unit, direction and current values always come from our data, not the model.
  * Rules ("mock-rules-v1"): deterministic fallback used when no key is set, or the LLM errors / times out /
    returns nothing valid. The fallback reason is returned to the UI and written to the audit log.
"""
import json
import math
import statistics
import uuid

from . import hr, llm, scoring, store

ENGINE = "mock-rules-v1"  # rules engine id (also the fallback)
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


def _rules_generate(u, card):
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


# ------------------------------------------------------------------ LLM engine

def active_engine():
    """Engine that will be tried first for the next generation."""
    return llm.label() or ENGINE


SYSTEM_PROMPT = """You are a KPI-setting assistant inside a commercial performance platform. A manager will review,
edit, accept or reject every suggestion you make; nothing you say is applied automatically.

Task: from 4 quarters of KPI history, propose at most 4 changes to ONE employee's KPI scorecard. Allowed types:
- "retarget": change the target of a KPI in current_kpis (e.g. target beaten every quarter so it no longer
  differentiates, or missed every quarter AND the peer median is also low so it looks unrealistic). Do NOT lower a
  target only because this individual is missing it while peers hit it.
- "reweight": change the weight_in_bucket (1-70) of a KPI in current_kpis, e.g. to focus on the KPI losing the most
  points. Only for a bucket that has more than one KPI. Other KPIs in the bucket are rebalanced automatically.
- "new_kpi": add a KPI from candidates (never one already in current_kpis), typically a leading indicator for a
  lagging KPI that is below target. Suggest a weight_in_bucket between 5 and 30.

Rules:
- Use only the numbers provided. Do not invent data. Cite the specific numbers that justify each suggestion.
- Targets are in the KPI's own unit. For direction "lower" (e.g. days), a tougher target is a smaller number.
- At most one suggestion per KPI. Fewer, well-justified suggestions beat many weak ones; an empty list is allowed.
- Be fair to the employee: distinguish an individual gap from a team-wide or target-calibration problem.
- confidence: "high" only when the 4-quarter pattern is consistent; otherwise "medium" or "low".

Respond with ONLY a JSON object, no prose and no markdown fences, in exactly this shape:
{"suggestions": [{"type": "retarget|reweight|new_kpi", "kpi": "<key>", "suggested_target": <number>,
  "suggested_weight": <integer>, "confidence": "high|medium|low",
  "reason": "<1-3 sentences a manager can read>", "evidence": ["<short fact with numbers>", "..."]}]}"""


def _llm_context(u, card):
    """Slim, de-identified view of build_context() for the prompt (no name, id, source labels or pay)."""
    ctx = build_context(u, card)
    cur = []
    for k in ctx["current_kpis"]:
        pm, n = hr._peer_median(u, k["key"])
        cur.append({"key": k["key"], "name": k["name"], "bucket": k["bucket"], "unit": k["unit"], "direction": k["direction"],
                    "target": k["target"], "actual": k["actual"], "attainment_pct": k["attainment_pct"],
                    "weight_in_bucket": k["weight_in_bucket"], "points_lost": k["points_lost"],
                    "history": k["history"], "peer_median_attainment_pct": round(pm, 1) if pm is not None else None,
                    "peers": n, "leading_indicators": hr.LEADING.get(k["key"], [])})
    cands = []
    for key, c in ctx["candidates"].items():
        peers = c["peer_actuals"]
        cands.append({"key": key, "name": c["name"], "bucket": c["bucket"], "unit": c["unit"], "direction": c["direction"],
                      "formula": c["formula"], "history": c["history"],
                      "peer_median_actual": statistics.median(peers) if len(peers) >= 3 else None})
    buckets = {}
    for k in ctx["current_kpis"]:
        buckets[k["bucket"]] = buckets.get(k["bucket"], 0) + 1
    return {"role": ctx["role"], "department": ctx["department"], "period": ctx["period"],
            "bucket_weights": ctx["weightage"], "kpis_per_bucket": buckets, "current_kpis": cur, "candidates": cands}


def _parse_json(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.index("\n") + 1:] if "\n" in t else t
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise llm.LLMError("LLM reply contained no JSON object")
    try:
        data = json.loads(t[a:b + 1])
    except ValueError:
        raise llm.LLMError("LLM reply was not valid JSON")
    items = data.get("suggestions") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise llm.LLMError("LLM JSON had no 'suggestions' list")
    return items


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _validate(raw_items, u, card, engine):
    """Keep only suggestions that are consistent with the real data. Everything descriptive comes from our data."""
    cur = {k["key"]: k for k in card["kpis"]}
    ctx = build_context(u, card)
    cands = ctx["candidates"]
    per_bucket = {}
    for k in card["kpis"]:
        per_bucket[k["bucket"]] = per_bucket.get(k["bucket"], 0) + 1
    out, seen = [], set()
    for r in raw_items:
        if len(out) >= MAX_SUGGESTIONS:
            break
        if not isinstance(r, dict):
            continue
        typ, key = r.get("type"), r.get("kpi")
        if typ not in ("retarget", "reweight", "new_kpi") or not isinstance(key, str) or key in seen:
            continue
        reason = r.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            continue
        tgt, w = r.get("suggested_target"), r.get("suggested_weight")
        if typ in ("retarget", "reweight"):
            if key not in cur:
                continue
            k = cur[key]
            seq = [h["attainment"] for h in k["history"]] + [k["attainment_pct"]]
            facts = ["4-quarter attainment: " + ", ".join("%.0f%%" % a for a in seq)]
            base = dict(name=k["name"], bucket=k["bucket"], unit=k["unit"], direction=k["direction"],
                        current_target=k["target"], current_weight=k["weight_in_bucket"])
            if typ == "retarget":
                if not _num(tgt) or tgt <= 0 or not 0.5 <= tgt / k["target"] <= 2.0:
                    continue  # missing or implausible (more than 2x away from the current target)
                tgt = _round_target(tgt, k["unit"])
                if tgt == k["target"]:
                    continue
                w = k["weight_in_bucket"]
            else:
                if per_bucket.get(k["bucket"], 0) < 2 or not _num(w) or int(w) != w or not 1 <= w <= 70 or w == k["weight_in_bucket"]:
                    continue
                tgt, w = k["target"], int(w)
        else:
            if key not in cands or key in cur:
                continue
            c = cands[key]
            hist = c["history"]
            own_mean = statistics.mean(h["actual"] for h in hist)
            if not _num(tgt) or tgt <= 0 or (own_mean > 0 and not 0.5 <= tgt / own_mean <= 2.0):
                continue  # implausible vs. the person's own history
            tgt = _round_target(tgt, c["unit"])
            w = int(w) if _num(w) and int(w) == w and 1 <= w <= 70 else NEW_KPI_WEIGHT
            facts = ["History: " + ", ".join("%s %s" % (h["period"], hr._fmt(h["actual"], c["unit"])) for h in hist),
                     "Formula: " + c["formula"]]
            base = dict(name=c["name"], bucket=c["bucket"], unit=c["unit"], direction=c["direction"],
                        current_target=None, current_weight=0)
        ev = [e.strip()[:200] for e in (r.get("evidence") or []) if isinstance(e, str) and e.strip()][:3]
        conf = r.get("confidence") if r.get("confidence") in ("high", "medium", "low") else "low"
        seen.add(key)
        out.append(dict(base, id="S-" + uuid.uuid4().hex[:8], type=typ, kpi=key, suggested_target=tgt, suggested_weight=w,
                        confidence=conf, reason=reason.strip()[:600], evidence=facts + ev,
                        status="pending_review", engine=engine, generated_at=store.now_iso(), edited=False, decided_by=None))
    return out


def _llm_generate(u, card):
    user = ("Employee scorecard context (JSON). Propose up to %d KPI changes.\n\n%s"
            % (MAX_SUGGESTIONS, json.dumps(_llm_context(u, card), separators=(",", ":"))))
    return _validate(_parse_json(llm.complete(SYSTEM_PROMPT, user)), u, card, llm.label())


def generate_with_meta(u, card):
    """Returns (suggestions, meta). meta = {"engine": used engine, "fallback": reason or None}."""
    if llm.configured():
        try:
            items = _llm_generate(u, card)
            if items:
                return items, {"engine": llm.label(), "fallback": None}
            reason = "LLM returned no valid suggestions"
        except llm.LLMError as e:
            reason = str(e)[:200]
        items = _rules_generate(u, card)
        for s in items:
            s["engine"] = ENGINE + " (fallback)"
        return items, {"engine": ENGINE, "fallback": reason}
    return _rules_generate(u, card), {"engine": ENGINE, "fallback": None}


def generate(u, card):
    return generate_with_meta(u, card)[0]

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
