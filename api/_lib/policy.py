"""Authorisation policy — the single place that decides who may see or change what.

Hierarchy: director > senior_manager > manager > employee.

Row-level scope (whose records):
  director        every employee
  senior_manager  every employee in their departments (scope_depts)
  manager         themselves + direct reports
  employee        themselves only

Field-level rules (which attributes) — enforced by the serialisers in router.py, which only ever add a sensitive
field after the matching capability check below passes:
  compensation (salary / bonus / commission / OTE / pay band) ... director only
  individual expense claims and individual cost / ROI ........... director only
  aggregated cost / ROI (team or department, n >= MIN_GROUP) ...... director, senior_manager (own departments)
  individual pulse / onboarding survey answers .................... director (HR/executive review) only
  team-aggregate survey themes (n >= MIN_GROUP) ................... manager, senior_manager, director
  HR intervention cases ........................................... manager (own team), senior_manager (own depts), director
"""
from . import store

LEVEL_RANK = {"employee": 0, "manager": 1, "senior_manager": 2, "director": 3}
MIN_GROUP = 5           # smallest group for which aggregates are shown (prevents re-identification)
MANAGER_FIN_BAND = 15   # a manager may set an individual's financial weight within ±15 pts of the role template


class Forbidden(Exception):
    pass


def rank(u):
    return LEVEL_RANK[u["level"]]


def direct_reports(uid):
    return [u for u in store.employees() if u.get("manager_id") == uid]


def visible_ids(actor):
    lvl = actor["level"]
    if lvl == "director":
        return {u["id"] for u in store.employees()}
    if lvl == "senior_manager":
        return {u["id"] for u in store.employees() if u["dept"] in actor["scope_depts"]}
    if lvl == "manager":
        return {actor["id"]} | {u["id"] for u in direct_reports(actor["id"])}
    return {actor["id"]}


def can_view(actor, target_id):
    return target_id in visible_ids(actor)


def require_view(actor, target_id):
    if not store.user(target_id) or not can_view(actor, target_id):
        # Same error whether the record exists or not — don't leak which ids are valid.
        raise Forbidden("Not found or not permitted")


def manages(actor, target_id):
    """True if actor sits above target in the hierarchy (and within scope). Nobody manages themselves."""
    if target_id == actor["id"] or not can_view(actor, target_id):
        return False
    return rank(actor) >= LEVEL_RANK["manager"]


def require_manage(actor, target_id):
    if not manages(actor, target_id):
        raise Forbidden("Only the employee's manager, senior manager or director can do this")


def can_see_compensation(actor):
    return actor["level"] == "director"


def can_see_individual_cost(actor):
    return actor["level"] == "director"


def can_see_aggregate_cost(actor):
    return actor["level"] in ("director", "senior_manager")


def can_see_individual_survey(actor):
    return actor["level"] == "director"


def can_see_company(actor):
    return actor["level"] == "director"


def visible_depts(actor):
    if actor["level"] == "director":
        return [d["name"] for d in store.seed()["departments"]]
    if actor["level"] == "senior_manager":
        return list(actor["scope_depts"])
    return [actor["dept"]] if actor.get("dept") else []


def can_edit_role_template(actor, rk):
    dept = rk.split("/", 1)[0]
    if actor["level"] == "director":
        return True
    return actor["level"] == "senior_manager" and dept in actor["scope_depts"]


def editable_role_keys(actor):
    keys = sorted({store.role_key(u) for u in store.employees()})
    return [k for k in keys if can_edit_role_template(actor, k)]


def fin_band_for(actor):
    """None = unbounded; otherwise max distance (pts) from the role template a manager may agree individually."""
    return None if rank(actor) >= LEVEL_RANK["senior_manager"] else MANAGER_FIN_BAND


def nav_for(actor):
    """Which dashboard sections the UI should render. Convenience only — every endpoint re-checks on its own."""
    lvl = actor["level"]
    if lvl == "employee":
        return ["my_performance"]
    tabs = []
    if lvl == "director":
        tabs += ["company", "departments", "roi", "individuals"]
    elif lvl == "senior_manager":
        tabs += ["departments", "roi"]
    tabs += ["performance", "hr", "weights", "ai_kpi"]
    if lvl in ("director", "senior_manager"):
        tabs += ["projects"]
    tabs += ["access", "methodology"]
    return tabs


ACCESS_MATRIX = [
    # (capability, director, senior_manager, manager, employee)
    ("Company financials (revenue, NGP, EBITDA, collections)", "Full", "—", "—", "—"),
    ("Department performance & cost coverage", "All departments", "Own departments", "—", "—"),
    ("ROI: individual cost base (salary + expenses + other)", "Full", "—", "—", "—"),
    ("ROI: team / department aggregates (n ≥ 5)", "All", "Own departments", "—", "—"),
    ("Salary, bonus/commission, OTE, pay band", "Full", "—", "—", "—"),
    ("Expense claims (line items)", "Full", "Totals only (aggregated)", "—", "—"),
    ("Individual KPI scorecards & gaps", "All", "Own departments", "Own team", "Own only"),
    ("KPI weightage — role templates", "Edit any role", "Edit roles in own departments", "View", "View own"),
    ("KPI weightage — individual agreed split / targets", "Edit", "Edit (own departments)", "Edit own team (±15 pts of template)", "View own"),
    ("AI KPI suggestions: generate / review / approve", "Yes", "Own departments", "Own team", "—"),
    ("HR intervention cases", "All + executive decisions", "Own departments (escalations)", "Own team", "Own improvement focus only"),
    ("Individual pulse / onboarding survey answers", "HR / executive review", "—", "—", "—"),
    ("Team survey themes (aggregated, n ≥ 5)", "Yes", "Yes", "Yes", "—"),
    ("Audit log", "All", "Own departments", "Own actions", "—"),
]
