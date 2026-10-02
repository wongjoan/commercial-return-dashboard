"""Builds api/_lib/data/seed.json — the anonymised demo dataset that the API serves (after filtering by role).

Inputs
  * The 64-person commercial roster structure from the original prototype (role, department, pay structure,
    pay amounts). Original names are NOT carried over — every person gets a fictional display name.
  * data/source/hr_dummy_dataset.xlsx — the synthetic HR dataset (employees, onboarding/pulse/exit surveys,
    action tracker). Each roster member is linked to one ACTIVE synthetic employee_id, matched on
    division and employee_group, and inherits that record's tenure, location and survey history.

Everything else (KPI targets/actuals/history, expense claims, statutory/benefit costs) is deterministic DUMMY
data from a fixed RNG seed, so re-running this script always produces the same file.

    python scripts/build_seed.py [path/to/hr_dummy_dataset.xlsx]
"""
import datetime as dt
import json
import os
import random
import sys

import openpyxl

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "source", "hr_dummy_dataset.xlsx")
OUT = os.path.join(ROOT, "api", "_lib", "data", "seed.json")
rng = random.Random(20261002)

# (role, dept, pay structure, base, variable) — order and figures from the original prototype; names removed.
ROSTER = [
    ("Sales Executive", "Sales", "Commission", 49200, 21086),
    ("Account Executive", "Sales", "Commission", 41200, 26899),
    ("Sales Representative", "Sales", "Commission", 45600, 12991),
    ("Account Executive", "Sales", "Commission", 45400, 11441),
    ("Sales Representative", "Sales", "Commission", 46500, 17321),
    ("Sales Representative", "Sales", "Commission", 51300, 25132),
    ("Account Executive", "Sales", "Commission", 44900, 19823),
    ("Account Executive", "Sales", "Commission", 46100, 30265),
    ("Account Executive", "Sales", "Commission", 50800, 26960),
    ("Account Executive", "Sales", "Commission", 51900, 25587),
    ("Sales Representative", "Sales", "Commission", 48400, 14820),
    ("Account Executive", "Sales", "Commission", 50100, 31127),
    ("Sales Representative", "Sales", "Commission", 50800, 28143),
    ("Junior Sales Executive", "Sales", "Commission", 55000, 25000),
    ("Junior Sales Executive", "Sales", "Commission", 52500, 20000),
    ("Account Executive", "Sales", "Commission", 40500, 11919),
    ("Account Executive", "Sales", "Commission", 40200, 26476),
    ("Sales Representative", "Sales", "Commission", 40800, 24921),
    ("Account Executive", "Sales", "Commission", 41600, 26420),
    ("Account Executive", "Sales", "Commission", 49200, 27542),
    ("Account Director", "Sales", "Commission", 118000, 40000),
    ("Account Director", "Sales", "Commission", 111800, 67393),
    ("Senior Sales Manager", "Sales", "Commission", 152000, 98000),
    ("Sales Manager", "Sales", "Commission", 88600, 38975),
    ("CRM Administrator", "Sales Support", "Bonus", 53600, 8933),
    ("CRM Administrator", "Sales Support", "Bonus", 43000, 7167),
    ("CRM Administrator", "Sales Support", "Bonus", 43500, 7250),
    ("Sales Support Specialist", "Sales Support", "Bonus", 54000, 9000),
    ("Sales Support Specialist", "Sales Support", "Bonus", 42000, 7000),
    ("CRM Administrator", "Sales Support", "Bonus", 48600, 8100),
    ("Sales Support Specialist", "Sales Support", "Bonus", 54000, 9000),
    ("Sales Support Lead", "Sales Support", "Bonus", 102600, 17100),
    ("Sales Support Lead", "Sales Support", "Bonus", 149500, 24917),
    ("Product Analyst", "Product Management", "Bonus", 47100, 7850),
    ("Associate Product Manager", "Product Management", "Bonus", 51900, 8650),
    ("Product Analyst", "Product Management", "Bonus", 53400, 8900),
    ("Associate Product Manager", "Product Management", "Bonus", 46200, 7700),
    ("Product Analyst", "Product Management", "Bonus", 40300, 6717),
    ("Associate Product Manager", "Product Management", "Bonus", 51700, 8617),
    ("Product Analyst", "Product Management", "Bonus", 48600, 8100),
    ("Associate Product Manager", "Product Management", "Bonus", 52000, 8667),
    ("Senior Product Manager", "Product Management", "Bonus", 114200, 19033),
    ("Product Manager", "Product Management", "Bonus", 94800, 16000),
    ("Senior Consultant", "Consulting", "Bonus", 50300, 8383),
    ("Consultant", "Consulting", "Bonus", 45600, 7600),
    ("Consultant", "Consulting", "Bonus", 43700, 7283),
    ("Consultant", "Consulting", "Bonus", 48700, 8117),
    ("Consultant", "Consulting", "Bonus", 51400, 8567),
    ("Consultant", "Consulting", "Bonus", 49300, 8217),
    ("Presales Consultant", "Consulting", "Bonus", 90000, 15000),
    ("Senior Consultant", "Consulting", "Bonus", 53900, 8983),
    ("Senior Consultant", "Consulting", "Bonus", 47700, 7950),
    ("Consultant", "Consulting", "Bonus", 51100, 8517),
    ("Senior Consultant", "Consulting", "Bonus", 41000, 6833),
    ("Practice Lead", "Consulting", "Bonus", 124900, 20817),
    ("Consulting Manager", "Consulting", "Bonus", 90000, 15000),
    ("Consulting Manager", "Consulting", "Bonus", 83400, 13900),
    ("Implementation Executive", "Delivery", "Bonus", 41400, 6900),
    ("Implementation Executive", "Delivery", "Bonus", 47000, 7833),
    ("Delivery Specialist", "Delivery", "Bonus", 52400, 8733),
    ("Delivery Specialist", "Delivery", "Bonus", 40900, 6817),
    ("Implementation Executive", "Delivery", "Bonus", 43100, 7183),
    ("Delivery Lead", "Delivery", "Bonus", 40560, 6760),
    ("Delivery Manager", "Delivery", "Bonus", 95000, 15833),
]

# Fictional display names — deliberately unrelated to the supplied roster.
PSEUDONYMS = [
    "Aria Wen", "Ben Osman", "Cara Lindqvist", "Dev Raman", "Elise Navarro", "Faiz Karim", "Gina Holt",
    "Hugo Brandt", "Ines Moreau", "Jae Park", "Kiran Bose", "Lena Vos", "Malik Idris", "Nora Quinn",
    "Omar Halim", "Pia Sorensen", "Quentin Hale", "Rina Takeda", "Sami Farouk", "Tara Mehta",
    "Umar Siddiq", "Vera Kowal", "Wren Abbott", "Xavier Lam", "Yasmin Noor", "Zane Corbett",
    "Alma Reyes", "Bryn Calloway", "Cyrus Vale", "Dina Petrov", "Eko Santoso", "Freya Dahl",
    "Gus Ferreira", "Hana Iqbal", "Ivo Marsh", "Juno Castell", "Kai Brennan", "Lia Sato",
    "Milo Varga", "Nadia Elkin", "Otto Lund", "Priya Desai", "Rafe Okafor", "Sana Mirza",
    "Theo Gallo", "Uma Kapoor", "Vik Arora", "Willa Shaw", "Yusuf Demir", "Zara Hollis",
    "Anya Fischer", "Boris Klein", "Celia Ortega", "Dario Rossi", "Esme Fairley", "Femi Adeyemi",
    "Greta Holm", "Hadi Rahimi", "Isla Montague", "Joel Ashby", "Kemal Aydin", "Luz Herrera",
    "Mira Kovacs", "Nils Berg",
]
assert len(PSEUDONYMS) == len(ROSTER)

DEPARTMENTS = [
    {"name": "Sales", "ngp_attributed_annual": 9240000, "color": "var(--series-1)"},
    {"name": "Product Management", "ngp_attributed_annual": 2770000, "color": "var(--series-2)"},
    {"name": "Consulting", "ngp_attributed_annual": 2770000, "color": "var(--series-3)"},
    {"name": "Sales Support", "ngp_attributed_annual": 1850000, "color": "var(--series-4)"},
    {"name": "Delivery", "ngp_attributed_annual": 1850000, "color": "var(--series-5)"},
]
# Which synthetic HR division each commercial department is drawn from.
DIVISION_FOR_DEPT = {"Sales": "Sales", "Sales Support": "Operations", "Product Management": "Marketing",
                     "Consulting": "Technology", "Delivery": "Operations"}

PERIOD = "2026-Q3"
HISTORY_PERIODS = ["2025-Q4", "2026-Q1", "2026-Q2"]

# key: (name, bucket, unit, direction, formula, sensitivity). Sensitivity compresses attainment noise for %
# measures that naturally sit near their ceiling (order accuracy can't swing ±30%).
KPI_LIBRARY = {
    "Sales": {
        "revenue": ("Revenue booked", "financial", "RM", "higher", "Sum of revenue on closed-won deals in the quarter", 1.0),
        "ngp": ("Net gross profit (NGP)", "financial", "RM", "higher", "Revenue − direct cost of sale on closed-won deals", 1.0),
        "collections": ("Collection rate", "financial", "%", "higher", "Cash collected ÷ amount billed", 0.35),
        "retention": ("Customer retention rate", "non_financial", "%", "higher", "(Existing customers − customer losses) ÷ starting customers", 0.4),
        "win_rate": ("Deal win rate", "non_financial", "%", "higher", "Deals won ÷ deals closed (won + lost)", 1.0),
        "pipeline_coverage": ("Pipeline coverage ratio", "non_financial", "x", "higher", "Qualified pipeline value ÷ remaining quota", 1.0),
        "new_logos": ("New logos acquired", "financial", "count", "higher", "Count of first-time customers closed-won", 1.0),
        "forecast_accuracy": ("Forecast accuracy", "non_financial", "%", "higher", "1 − |forecast − actual| ÷ actual", 0.5),
    },
    "Sales Support": {
        "renewal_value": ("Renewal value processed on time", "financial", "RM", "higher", "Contract value of renewals finalised before deadline", 1.0),
        "order_accuracy": ("Order accuracy", "non_financial", "%", "higher", "Orders without error ÷ total orders", 0.15),
        "renewal_completion": ("Renewal admin completion", "non_financial", "%", "higher", "Renewals finalised before deadline ÷ total renewals due", 0.3),
        "turnaround": ("Quote turnaround time", "non_financial", "days", "lower", "Average working days from request to issued quote", 1.0),
        "internal_csat": ("Internal CSAT", "non_financial", "%", "higher", "Sum of internal survey ratings ÷ maximum possible rating", 0.5),
        "first_time_right": ("First-time-right quotes", "non_financial", "%", "higher", "Quotes issued without rework ÷ total quotes", 0.3),
    },
    "Product Management": {
        "attributed_ngp": ("Attributed NGP", "financial", "RM", "higher", "Product Management's attributed share of deal NGP", 1.0),
        "deal_support_win": ("Deal support win rate", "non_financial", "%", "higher", "Supported deals won ÷ total supported deals", 1.0),
        "sla_compliance": ("SLA compliance", "non_financial", "%", "higher", "Requests resolved within SLA ÷ total requests", 0.4),
        "roadmap_on_time": ("Roadmap delivery on time", "non_financial", "%", "higher", "Roadmap items shipped by committed date ÷ items committed", 0.6),
        "enablement_sessions": ("Sales-enablement sessions", "non_financial", "count", "higher", "Enablement sessions delivered to Sales in the quarter", 1.0),
    },
    "Consulting": {
        "chargeable_revenue": ("Chargeable revenue", "financial", "RM", "higher", "Billable hours × rate, invoiced in the quarter", 1.0),
        "utilisation": ("Utilisation rate", "non_financial", "%", "higher", "Chargeable hours ÷ total working hours", 0.8),
        "scoping_accuracy": ("Scoping accuracy", "non_financial", "%", "higher", "1 − |actual effort − scoped effort| ÷ scoped effort", 0.6),
        "delivery_quality": ("Delivery quality score", "non_financial", "%", "higher", "Average client quality rating ÷ maximum rating", 0.5),
        "scoping_turnaround": ("Scoping turnaround", "non_financial", "days", "lower", "Average working days from request to signed-off scope", 1.0),
    },
    "Delivery": {
        "project_margin": ("Project margin", "financial", "%", "higher", "Project NGP ÷ project revenue", 0.7),
        "dso": ("Days sales outstanding (DSO)", "financial", "days", "lower", "Receivables ÷ revenue × days in period, on owned projects", 1.0),
        "csat": ("Customer CSAT", "non_financial", "%", "higher", "Sum of survey ratings ÷ maximum possible rating", 0.5),
        "on_time": ("On-time delivery", "non_financial", "%", "higher", "Milestones delivered on time ÷ milestones delivered", 0.6),
        "defect_escape": ("Defect escape rate", "non_financial", "%", "lower", "Defects found after go-live ÷ total defects", 1.0),
        "change_request_margin": ("Change-request margin", "financial", "%", "higher", "Change-request NGP ÷ change-request revenue", 0.7),
    },
}

# Default role templates per department: financial weight + KPI weights inside each bucket (each bucket sums to 100).
DEPT_TEMPLATES = {
    "Sales": {"financial_weight": 60, "kpi_weights": {"financial": {"revenue": 50, "ngp": 30, "collections": 20}, "non_financial": {"retention": 60, "win_rate": 40}}},
    "Sales Support": {"financial_weight": 10, "kpi_weights": {"financial": {"renewal_value": 100}, "non_financial": {"order_accuracy": 35, "renewal_completion": 35, "turnaround": 30}}},
    "Product Management": {"financial_weight": 35, "kpi_weights": {"financial": {"attributed_ngp": 100}, "non_financial": {"deal_support_win": 55, "sla_compliance": 45}}},
    "Consulting": {"financial_weight": 20, "kpi_weights": {"financial": {"chargeable_revenue": 100}, "non_financial": {"utilisation": 50, "scoping_accuracy": 50}}},
    "Delivery": {"financial_weight": 30, "kpi_weights": {"financial": {"project_margin": 60, "dso": 40}, "non_financial": {"csat": 50, "on_time": 50}}},
}

FIXED_TARGETS = {
    "collections": 90, "retention": 92, "win_rate": 30, "pipeline_coverage": 3.0, "new_logos": 3, "forecast_accuracy": 85,
    "renewal_value": 150000, "order_accuracy": 98, "renewal_completion": 95, "turnaround": 2.0, "internal_csat": 85, "first_time_right": 95,
    "deal_support_win": 40, "sla_compliance": 90, "roadmap_on_time": 85, "enablement_sessions": 6,
    "utilisation": 75, "scoping_accuracy": 85, "delivery_quality": 88, "scoping_turnaround": 5.0,
    "project_margin": 35, "dso": 45, "csat": 85, "on_time": 90, "defect_escape": 5.0, "change_request_margin": 30,
}


def target_for(key, ote):
    if key in FIXED_TARGETS:
        return FIXED_TARGETS[key]
    return {"revenue": round(ote * 2.6, -3), "ngp": round(ote * 2.6 * 0.44, -3),
            "attributed_ngp": round(ote * 1.15, -3), "chargeable_revenue": round(ote * 2.2, -3)}[key]


def actual_for(meta, target, attainment):
    unit, direction = meta[2], meta[3]
    val = target / max(attainment, 0.4) if direction == "lower" else target * attainment
    if unit == "%":
        return round(min(100.0, val), 1)
    if unit == "RM":
        return round(val, -2)
    if unit == "count":
        return max(0, round(val))
    return round(val, 2)


def iso(v):
    return v.date().isoformat() if isinstance(v, dt.datetime) else v


def load_hr(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    def rows(sheet):
        it = wb[sheet].iter_rows(values_only=True)
        head = [str(h).strip().lower() for h in next(it)]
        return [dict(zip(head, r)) for r in it if r and r[0]]

    employees = rows("Employees")
    pulse, onboarding = {}, {}
    for r in rows("Pulse_Survey"):
        pulse.setdefault(r["employee_id"], []).append({
            "wave": r["survey_wave"], "date": iso(r["survey_date"]), "engagement": r["engagement_score"],
            "manager_support": r["manager_support"], "workload": r["workload"], "career_growth": r["career_growth"],
            "belonging": r["belonging"], "comment": r["comment"]})
    for r in rows("Onboarding_Survey"):
        onboarding[r["employee_id"]] = {"date": iso(r["survey_date"]), "overall": r["overall_score"], "manager_support": r["manager_support"],
                                        "role_clarity": r["role_clarity"], "tools_access": r["tools_access"], "comment": r["comment"]}
    actions = [{k: iso(v) for k, v in r.items()} for r in rows("Action_Tracker")]
    exits = [r for r in employees if r["exit_status"] == "Left"]
    for v in pulse.values():
        v.sort(key=lambda w: w["date"])
    return employees, pulse, onboarding, actions, exits


def main():
    hr_employees, pulse, onboarding, actions, exits = load_hr(SRC)

    users = [{"id": None, "name": PSEUDONYMS[i], "title": r[0], "dept": r[1], "pay_structure": r[2]} for i, r in enumerate(ROSTER)]
    by_title = {}
    for u in users:
        by_title.setdefault((u["dept"], u["title"]), []).append(u)
    director = {"id": "D0001", "name": "Morgan Ellery", "title": "Commercial Director", "dept": None, "level": "director",
                "manager_id": None, "scope_depts": [d["name"] for d in DEPARTMENTS], "hr_link": None}

    def one(dept, title, idx=0):
        return by_title[(dept, title)][idx]

    # ---- reporting hierarchy: Director -> Senior Manager -> Manager -> Employee ----
    sm_sales, sm_product, sm_practice = one("Sales", "Senior Sales Manager"), one("Product Management", "Senior Product Manager"), one("Consulting", "Practice Lead")
    mgr = {"Sales": one("Sales", "Sales Manager"), "Sales Support": one("Sales Support", "Sales Support Lead", 0),
           "Product Management": one("Product Management", "Product Manager"), "Delivery": one("Delivery", "Delivery Manager")}
    mgr_cons = [one("Consulting", "Consulting Manager", 0), one("Consulting", "Consulting Manager", 1)]
    for sm, depts in ((sm_sales, ["Sales", "Sales Support"]), (sm_product, ["Product Management"]), (sm_practice, ["Consulting", "Delivery"])):
        sm.update(level="senior_manager", scope_depts=depts, _mgr=director)
    for m, sm in ((mgr["Sales"], sm_sales), (mgr["Sales Support"], sm_sales), (mgr["Product Management"], sm_product),
                  (mgr_cons[0], sm_practice), (mgr_cons[1], sm_practice), (mgr["Delivery"], sm_practice)):
        m.update(level="manager", scope_depts=[m["dept"]], _mgr=sm)
    consultants = [u for u in users if u["dept"] == "Consulting" and "level" not in u]
    for u in users:
        if "level" in u:
            continue
        u.update(level="employee", scope_depts=[])
        if u["dept"] == "Sales Support" and u["title"] == "Sales Support Lead":
            u["_mgr"] = sm_sales
        elif u["dept"] == "Consulting":
            u["_mgr"] = mgr_cons[consultants.index(u) % 2]
        else:
            u["_mgr"] = mgr[u["dept"]]

    # ---- link each roster member to an ACTIVE synthetic HR record (division + employee_group) ----
    lead_titles = {"Account Director", "Sales Support Lead", "Delivery Lead"}
    pool = {}
    for r in hr_employees:
        if r["exit_status"] == "Active":
            pool.setdefault((r["division"], r["employee_group"]), []).append(r)
    for k in pool:  # prefer records with the most pulse waves, then by id — deterministic
        pool[k].sort(key=lambda r: (-len(pulse.get(r["employee_id"], [])), r["employee_id"]))
    for u in users:
        group = "Manager" if u["level"] in ("manager", "senior_manager") else ("Team Lead" if u["title"] in lead_titles else "Individual Contributor")
        rec = pool[(DIVISION_FOR_DEPT[u["dept"]], group)].pop(0)
        u["id"] = rec["employee_id"]
        u["hr_link"] = {"source_division": rec["division"], "employee_group": rec["employee_group"], "location": rec["location"],
                        "join_date": iso(rec["join_date"]), "tenure_months": int(rec["tenure_months"])}
    for u in users:
        u["manager_id"] = u.pop("_mgr")["id"]

    # ---- compensation + other employment costs (SENSITIVE: Director-only via API policy) ----
    compensation, other_costs = {}, {}
    tool_cost = {"Sales": 2400, "Sales Support": 1500, "Product Management": 1800, "Consulting": 1800, "Delivery": 1500}
    for u, (role, dept, pay, base, variable) in zip(users, ROSTER):
        compensation[u["id"]] = {"pay_structure": pay, "base_annual": base, "variable_annual": variable, "ote_annual": base + variable}
        other_costs[u["id"]] = {"employer_statutory_annual": round(base * (0.12 if base / 12 > 5000 else 0.13) + 1100),  # EPF + SOCSO/EIS (approx.)
                                "benefits_annual": 3600 if base < 100000 else 6000, "tools_licences_annual": tool_cost[dept]}

    # ---- KPI history + current-period actuals (DUMMY), loosely coupled to the linked pulse data ----
    # "strain" = rising workload / falling engagement in the linked pulse survey; it nudges performance down so the
    # HR-intervention cause analysis has something coherent to find. A few profiles are forced weak/declining.
    weak = {7, 15, 10, 25, 37, 45, 58}
    declining = {2, 17, 28, 44, 57, 10}
    improving = {1, 8, 34, 47}
    kpi_records = {}
    for i, u in enumerate(users):
        waves = pulse.get(u["id"], [])
        strain = 0.0
        if waves:
            last = waves[-1]
            # numeric workload is ~always 1.0 in the supplied dataset, so a workload comment counts as a signal too
            workload_hit = last["workload"] >= 4.0 or "workload" in (last["comment"] or "").lower()
            strain = (0.04 if workload_hit else 0.0) + max(0.0, (3.0 - last["engagement"]) * 0.05)
        ote = compensation[u["id"]]["ote_annual"]
        pf = rng.uniform(0.74, 1.06) - strain
        if i in weak:
            pf = rng.uniform(0.56, 0.68)
        trend = -0.06 if i in declining else (0.05 if i in improving else rng.uniform(-0.015, 0.015))
        rec = {}
        for key, meta in KPI_LIBRARY[u["dept"]].items():
            target = target_for(key, ote)
            bias = rng.uniform(-0.10, 0.10)
            hist = []
            for qi, q in enumerate(HISTORY_PERIODS + [PERIOD]):
                raw = pf + bias - trend * (3 - qi) + rng.uniform(-0.05, 0.05)
                hist.append({"period": q, "target": target, "actual": actual_for(meta, target, 1 + (raw - 1) * meta[5])})
            rec[key] = {"target": target, "actual": hist[-1]["actual"], "history": hist[:-1]}
        kpi_records[u["id"]] = rec

    # ---- contribution (the "return" side of ROI), current quarter ----
    contribution = {}
    for d in DEPARTMENTS:
        members = [u for u in users if u["dept"] == d["name"]]
        if d["name"] == "Sales":
            for u in members:
                r = kpi_records[u["id"]]
                contribution[u["id"]] = {"basis": "Own closed-won NGP (CRM)", "ngp": r["ngp"]["actual"], "revenue": r["revenue"]["actual"]}
            continue
        fin_keys = list(DEPT_TEMPLATES[d["name"]]["kpi_weights"]["financial"])

        def fin_att(u):
            r, vals = kpi_records[u["id"]], []
            for k in fin_keys:
                t, a = r[k]["target"], r[k]["actual"]
                vals.append(t / a if KPI_LIBRARY[d["name"]][k][3] == "lower" else a / t)
            return sum(vals) / len(vals)
        w = {u["id"]: compensation[u["id"]]["ote_annual"] * fin_att(u) for u in members}
        tot = sum(w.values())
        for u in members:
            contribution[u["id"]] = {"basis": "Attributed share of " + d["name"] + " NGP (attribution matrix, weighted by financial KPI attainment)",
                                     "ngp": round(d["ngp_attributed_annual"] / 4 * w[u["id"]] / tot, -2), "revenue": None}

    # ---- expense claims, current quarter (SENSITIVE: Director-only). Cost structure differs by department. ----
    profiles = {
        "Sales": {"Client entertainment": (2, 5, 150, 900), "Travel": (1, 3, 200, 1500), "Other sales expenses": (0, 2, 80, 500)},
        "Sales Support": {"Other sales expenses": (0, 1, 50, 250)},
        "Product Management": {"Travel": (0, 2, 200, 1200), "Client entertainment": (0, 1, 100, 400), "Training": (0, 1, 500, 2500)},
        "Consulting": {"Travel": (2, 5, 300, 2000), "Client entertainment": (0, 1, 100, 450), "Training": (0, 1, 500, 2500)},
        "Delivery": {"Travel": (1, 3, 200, 1400), "Other sales expenses": (0, 1, 50, 300)},
    }
    expenses, n = [], 0
    for u in users:
        seniority = 1.6 if u["level"] in ("manager", "senior_manager") or "Director" in u["title"] else 1.0
        for cat, (lo, hi, amin, amax) in profiles[u["dept"]].items():
            for _ in range(rng.randint(lo, hi)):
                n += 1
                expenses.append({"id": "X%04d" % n, "employee_id": u["id"], "category": cat,
                                 "date": "%s-%02d" % (rng.choice(["2026-07", "2026-08", "2026-09"]), rng.randint(1, 28)),
                                 "amount": round(rng.uniform(amin, amax) * seniority, 2),
                                 "status": "approved" if rng.random() > 0.12 else "pending"})

    # ---- HR context from the synthetic dataset (SENSITIVE: individual survey data is HR/Director-only) ----
    linked = {u["id"] for u in users}
    hr_context = {"pulse": {k: v for k, v in pulse.items() if k in linked},
                  "onboarding": {k: v for k, v in onboarding.items() if k in linked},
                  "actions": actions,
                  "attrition_by_division": {}}
    for r in exits:
        a = hr_context["attrition_by_division"].setdefault(r["division"], {"leavers": 0, "reasons": {}})
        a["leavers"] += 1
        a["reasons"][r["exit_reason"]] = a["reasons"].get(r["exit_reason"], 0) + 1
    for r in hr_employees:
        hr_context["attrition_by_division"].setdefault(r["division"], {"leavers": 0, "reasons": {}}).setdefault("headcount", 0)
        hr_context["attrition_by_division"][r["division"]]["headcount"] += 1

    # ---- company-level illustrative financials + projects (Director-only), unchanged from the original prototype ----
    company = {
        "monthly": [["Jan", 2600000, 1144000, 598000, 0.90], ["Feb", 2700000, 1188000, 621000, 0.90], ["Mar", 3100000, 1364000, 713000, 0.92],
                    ["Apr", 3000000, 1320000, 690000, 0.93], ["May", 3300000, 1452000, 759000, 0.93], ["Jun", 3500000, 1540000, 805000, 0.94],
                    ["Jul", 3400000, 1496000, 782000, 0.95], ["Aug", 3500000, 1540000, 805000, 0.95], ["Sep", 3700000, 1628000, 851000, 0.96],
                    ["Oct", 4000000, 1760000, 920000, 0.96], ["Nov", 4300000, 1892000, 989000, 0.97], ["Dec", 4900000, 2156000, 1127000, 0.98]],
        "quarterly": [["Q1", 8400000, 3696000, 1932000, 0.92], ["Q2", 9800000, 4312000, 2254000, 0.93], ["Q3", 10600000, 4664000, 2438000, 0.95], ["Q4", 13200000, 5808000, 3036000, 0.97]],
        "annual": [["FY2025", 36500000, 15600000, 7900000, 0.912], ["FY2026", 42000000, 18480000, 9660000, 0.946]],
    }
    projects = [
        {"name": "Project Atlas", "type": "ERP Implementation", "status": "Active", "revenue": 1200000, "cost": 780000, "ngp": 420000, "collected": 1020000, "dso": 62,
         "attribution": [["Sales", 40], ["Consulting", 30], ["Delivery", 20], ["Product Management", 10]], "flag": None},
        {"name": "Project Horizon", "type": "Cloud Migration", "status": "Active", "revenue": 2050000, "cost": 1350000, "ngp": 700000, "collected": 1989000, "dso": 38,
         "attribution": [["Sales", 35], ["Delivery", 35], ["Consulting", 20], ["Sales Support", 10]], "flag": None},
        {"name": "Project Beacon", "type": "Managed Services Renewal", "status": "Complete", "revenue": 860000, "cost": 520000, "ngp": 340000, "collected": 860000, "dso": 21,
         "attribution": [["Sales", 55], ["Sales Support", 25], ["Product Management", 20]], "flag": None},
        {"name": "Project Falcon", "type": "New Logo — Complex Solution", "status": "Active", "revenue": 1680000, "cost": 1220000, "ngp": 460000, "collected": 1193000, "dso": 96,
         "attribution": [["Sales", 30], ["Consulting", 35], ["Product Management", 15], ["Delivery", 20]], "flag": "NGP margin 27.4% vs. 35% target; collections at 71%, DSO 96 days"},
    ]

    library = {d: {k: {"name": m[0], "bucket": m[1], "unit": m[2], "direction": m[3], "formula": m[4]} for k, m in kpis.items()} for d, kpis in KPI_LIBRARY.items()}
    templates = {}
    for u in users:
        templates.setdefault(u["dept"] + "/" + u["title"], json.loads(json.dumps(DEPT_TEMPLATES[u["dept"]])))

    seed = {
        "meta": {"dataset": "Anonymised demo dataset: fictional names, synthetic HR records (hr_dummy_dataset.xlsx), dummy KPI/expense/cost data",
                 "period": PERIOD, "history_periods": HISTORY_PERIODS, "currency": "RM", "generated_by": "scripts/build_seed.py"},
        "departments": DEPARTMENTS,
        "users": [director] + users,
        "compensation": compensation,
        "other_costs": other_costs,
        "expenses": expenses,
        "kpi_library": library,
        "role_templates": templates,
        "kpi_records": kpi_records,
        "contribution": contribution,
        "hr_context": hr_context,
        "company": company,
        "projects": projects,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(seed, f, indent=1, ensure_ascii=False, default=str)
    print("wrote", os.path.relpath(OUT, ROOT), "-", len(users), "employees,", len(expenses), "expense claims,",
          len(hr_context["pulse"]), "linked pulse histories")


if __name__ == "__main__":
    main()
