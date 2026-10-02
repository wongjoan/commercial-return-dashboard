"""ROI on the full employee cost base, for the current quarter.

    Return (contribution)  = attributed NGP — own closed-won NGP for Sales; for other functions, the
                             department's attributed NGP share (attribution matrix) weighted by financial KPI attainment
    Salary cost            = base salary ÷ 4
    Variable pay           = bonus accrual (bonus staff: annual bonus ÷ 4)
                             or commission earned (commission staff: annual commission ÷ 4 × revenue attainment, 50–150%)
    Expense cost           = APPROVED claims in the quarter: client entertainment, travel, other sales expenses, training
    Other employment cost  = employer statutory (EPF/SOCSO/EIS) + benefits + tools/licences, ÷ 4
    Total cost             = salary + variable + expenses + other
    ROI                    = (Return − Total cost) ÷ Total cost
    Cost coverage          = Return ÷ Total cost         (the original prototype's ratio, kept for continuity)

Cost structures differ per person: commission vs bonus, department expense profile, statutory rate by salary level.
"""
from . import store

EXPENSE_CATEGORIES = ["Client entertainment", "Travel", "Other sales expenses", "Training"]


def _commission_factor(uid):
    rec = store.seed()["kpi_records"][uid]
    if "revenue" not in rec:
        return 1.0
    att = rec["revenue"]["actual"] / rec["revenue"]["target"]
    return max(0.5, min(1.5, att))


def individual(uid):
    s = store.seed()
    comp, oc = s["compensation"][uid], s["other_costs"][uid]
    salary = comp["base_annual"] / 4
    if comp["pay_structure"] == "Commission":
        variable = comp["variable_annual"] / 4 * _commission_factor(uid)
        variable_basis = "Commission earned (annual commission ÷ 4 × revenue attainment)"
    else:
        variable = comp["variable_annual"] / 4
        variable_basis = "Bonus accrual (annual bonus ÷ 4)"
    claims = [e for e in s["expenses"] if e["employee_id"] == uid]
    by_cat = {c: 0.0 for c in EXPENSE_CATEGORIES}
    pending = 0.0
    for e in claims:
        if e["status"] == "approved":
            by_cat[e["category"]] = by_cat.get(e["category"], 0.0) + e["amount"]
        else:
            pending += e["amount"]
    expenses = sum(by_cat.values())
    other = {"employer_statutory": oc["employer_statutory_annual"] / 4, "benefits": oc["benefits_annual"] / 4,
             "tools_licences": oc["tools_licences_annual"] / 4}
    other_total = sum(other.values())
    total = salary + variable + expenses + other_total
    contrib = s["contribution"][uid]
    ret = contrib["ngp"]
    return {
        "return": round(ret), "return_basis": contrib["basis"], "revenue": contrib.get("revenue"),
        "salary_cost": round(salary), "variable_cost": round(variable), "variable_basis": variable_basis,
        "expense_cost": round(expenses), "expenses_by_category": {k: round(v) for k, v in by_cat.items()},
        "expenses_pending": round(pending),
        "other_cost": round(other_total), "other_breakdown": {k: round(v) for k, v in other.items()},
        "total_cost": round(total),
        "roi_pct": round((ret - total) / total * 100, 1),
        "coverage": round(ret / total, 2),
    }


def aggregate(uids):
    rows = [individual(u) for u in uids]
    keys = ["return", "salary_cost", "variable_cost", "expense_cost", "other_cost", "total_cost", "expenses_pending"]
    agg = {k: sum(r[k] for r in rows) for k in keys}
    agg["expenses_by_category"] = {c: sum(r["expenses_by_category"][c] for r in rows) for c in EXPENSE_CATEGORIES}
    agg["headcount"] = len(rows)
    tc = agg["total_cost"] or 1
    agg["roi_pct"] = round((agg["return"] - tc) / tc * 100, 1)
    agg["coverage"] = round(agg["return"] / tc, 2)
    return agg


def claims_for(uid):
    return [e for e in store.seed()["expenses"] if e["employee_id"] == uid]
