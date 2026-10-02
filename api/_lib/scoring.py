"""Explainable KPI scoring.

    KPI attainment      = actual ÷ target              (or target ÷ actual when lower is better, e.g. DSO)
    KPI credit          = min(attainment, SCORE_CAP)   (over-achievement is shown, but not scored above 100%)
    KPI points possible = bucket weight × KPI weight within bucket
                          e.g. Financial 60% × Revenue 50% = 30 pts
    KPI points earned   = points possible × credit
    Bucket score        = Σ points earned in the bucket          (e.g. Financial 50 / 60 pts)
    Overall score       = Financial points + Non-financial points (e.g. 50 + 32 = 82%)

Every number in the response carries the inputs used to produce it, so the UI can show the whole calculation.
"""
from . import store

SCORE_CAP = 1.0
BUCKETS = ("financial", "non_financial")
BUCKET_LABEL = {"financial": "Financial", "non_financial": "Non-financial"}


class ConfigInvalid(Exception):
    pass


def band(pct):
    if pct < 70:
        return {"label": "Needs improvement", "kind": "critical"}
    if pct < 90:
        return {"label": "Meeting most expectations", "kind": "warning"}
    return {"label": "Meeting or exceeding expectations", "kind": "good"}


def library(dept):
    return store.seed()["kpi_library"][dept]


def effective_config(u):
    tpl = store.role_template(store.role_key(u))
    ov = store.employee_override(u["id"])
    cfg = {
        "financial_weight": ov.get("financial_weight", tpl["financial_weight"]),
        "kpi_weights": ov.get("kpi_weights") or tpl["kpi_weights"],
        "targets": ov.get("targets", {}),
        "kpi_sources": ov.get("kpi_sources", {}),
        "template": tpl,
        "individual": bool(ov),
        "agreed_by": ov.get("agreed_by"),
        "agreed_at": ov.get("agreed_at"),
        "agreement_note": ov.get("reason"),
    }
    return cfg


def validate_config(dept, financial_weight, kpi_weights):
    lib = library(dept)
    if not isinstance(financial_weight, int) or not 0 <= financial_weight <= 100:
        raise ConfigInvalid("Financial weight must be a whole number from 0 to 100")
    if not isinstance(kpi_weights, dict) or set(kpi_weights) != set(BUCKETS):
        raise ConfigInvalid("kpi_weights must contain 'financial' and 'non_financial'")
    for b in BUCKETS:
        bw = financial_weight if b == "financial" else 100 - financial_weight
        weights = kpi_weights[b]
        if not isinstance(weights, dict):
            raise ConfigInvalid("Bucket weights must be an object")
        for k, w in weights.items():
            if k not in lib:
                raise ConfigInvalid("Unknown KPI '%s' for %s" % (k, dept))
            if lib[k]["bucket"] != b:
                raise ConfigInvalid("KPI '%s' belongs to the %s bucket" % (k, BUCKET_LABEL[lib[k]["bucket"]]))
            if not isinstance(w, int) or not 0 <= w <= 100:
                raise ConfigInvalid("KPI weights must be whole numbers from 0 to 100")
        if bw > 0 and sum(weights.values()) != 100:
            raise ConfigInvalid("%s KPI weights must add up to 100 (currently %d)" % (BUCKET_LABEL[b], sum(weights.values())))


def _attainment(direction, target, actual):
    if direction == "lower":
        return target / actual if actual else 0.0
    return actual / target if target else 0.0


def scorecard(u, cfg=None):
    """Full explainable scorecard for one employee. Contains NO compensation or cost data."""
    cfg = cfg or effective_config(u)
    lib = library(u["dept"])
    rec = store.seed()["kpi_records"][u["id"]]
    meta = store.seed()["meta"]
    fin_w = cfg["financial_weight"]
    kpis, buckets = [], {}
    for b in BUCKETS:
        bw = fin_w if b == "financial" else 100 - fin_w
        earned = 0.0
        for k, w in cfg["kpi_weights"][b].items():
            if w == 0:
                continue
            m, r = lib[k], rec[k]
            target = cfg["targets"].get(k, r["target"])
            actual = r["actual"]
            att = _attainment(m["direction"], target, actual)
            possible = bw * w / 100.0
            pts = possible * min(att, SCORE_CAP)
            earned += pts
            hist = [{"period": h["period"], "target": cfg["targets"].get(k, h["target"]), "actual": h["actual"],
                     "attainment": round(_attainment(m["direction"], cfg["targets"].get(k, h["target"]), h["actual"]) * 100, 1)}
                    for h in r["history"]]
            src = cfg["kpi_sources"].get(k) or {"type": "role_template", "label": "Role KPI template"}
            kpis.append({
                "key": k, "name": m["name"], "bucket": b, "unit": m["unit"], "direction": m["direction"], "formula": m["formula"],
                "target": target, "actual": actual,
                "gap": round(actual - target, 2), "gap_pct": round((att - 1) * 100, 1),
                "attainment_pct": round(att * 100, 1), "progress_pct": round(min(att, 1.0) * 100, 1),
                "weight_in_bucket": w, "bucket_weight": bw, "weight_overall": round(possible, 2),
                "points_possible": round(possible, 2), "points_earned": round(pts, 2), "points_lost": round(possible - pts, 2),
                "history": hist, "source": src,
            })
        buckets[b] = {"label": BUCKET_LABEL[b], "weight": bw, "points": round(earned, 1),
                      "attainment_pct": round(earned / bw * 100, 1) if bw else None}
    overall = round(sum(b["points"] for b in buckets.values()), 1)

    # Same weights applied to past quarters -> trend (explains "is this new or ongoing?")
    trend = []
    for qi, period in enumerate(meta["history_periods"]):
        tot = 0.0
        for kp in kpis:
            h = kp["history"][qi]
            tot += kp["points_possible"] * min(h["attainment"] / 100.0, SCORE_CAP)
        trend.append({"period": period, "overall": round(tot, 1)})
    trend.append({"period": meta["period"], "overall": overall})

    ranked = sorted(kpis, key=lambda k: -k["points_lost"])
    biggest = ranked[0] if ranked and ranked[0]["points_lost"] > 0.05 else None
    return {
        "employee": {"id": u["id"], "name": u["name"], "title": u["title"], "dept": u["dept"], "level": u["level"],
                     "manager_id": u.get("manager_id")},
        "period": meta["period"],
        "overall": overall, "band": band(overall),
        "buckets": buckets,
        "kpis": kpis,
        "biggest_gap": {"key": biggest["key"], "name": biggest["name"], "points_lost": biggest["points_lost"]} if biggest else None,
        "trend": trend,
        "weightage": {"financial": fin_w, "non_financial": 100 - fin_w,
                      "template_financial": cfg["template"]["financial_weight"],
                      "individual": cfg["individual"], "agreed_by": cfg["agreed_by"], "agreed_at": cfg["agreed_at"],
                      "agreement_note": cfg["agreement_note"]},
        "formula": {
            "kpi": "points = bucket weight × KPI weight × min(actual ÷ target, 100%)  (target ÷ actual when lower is better)",
            "overall": "overall = financial points + non-financial points",
            "cap": "Over-achievement is shown but each KPI is capped at 100% credit",
        },
    }


def summary(card):
    return {"employee": card["employee"], "overall": card["overall"], "band": card["band"],
            "financial": card["buckets"]["financial"], "non_financial": card["buckets"]["non_financial"],
            "biggest_gap": card["biggest_gap"], "trend": [t["overall"] for t in card["trend"]],
            "weightage": {"financial": card["weightage"]["financial"], "individual": card["weightage"]["individual"]}}
