"""HTTP API. Every endpoint authenticates from the session cookie, then authorises with policy.* before reading data.

Responses are built field-by-field: sensitive attributes are only added after the matching policy check, so there is
no "fetch everything then hide it in the browser" path. See SECURITY.md.
"""
import json
import statistics
from urllib.parse import parse_qs, urlparse

from . import ai_kpi, auth, hr, policy, roi, scoring, store

FEATURED = ["D0001", "E0078", "E0019", "E0348", "E0308", "E0009"]  # Director, 2 SMs, 3 managers; employees pickable


class Request:
    def __init__(self, method, url, headers, body, ip="?", secure=False):
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        route = q.pop("route", None)
        path = "/api/" + route.strip("/") if route is not None else u.path.rstrip("/")
        self.method, self.path, self.query = method.upper(), path, q
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.raw_body, self.ip, self.secure = body or b"", ip, secure
        self.user = None

    def json(self):
        if not self.raw_body:
            return {}
        try:
            data = json.loads(self.raw_body.decode("utf-8"))
        except ValueError:
            raise ValueError("Body must be valid JSON")
        if not isinstance(data, dict):
            raise ValueError("Body must be a JSON object")
        return data


class HttpError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


ROUTES = {}


def route(method, path, auth_required=True):
    def deco(fn):
        ROUTES[(method, path)] = (fn, auth_required)
        return fn
    return deco


def _json(status, obj, extra_headers=None):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    headers = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store"),
               ("X-Content-Type-Options", "nosniff")] + (extra_headers or [])
    return status, headers, body


def dispatch(req):
    try:
        entry = ROUTES.get((req.method, req.path))
        if not entry:
            raise HttpError(404, "Unknown endpoint")
        fn, needs_auth = entry
        if req.method != "GET":
            # CSRF defence in depth (cookie is already SameSite=Strict): JSON only + same-origin.
            if "application/json" not in req.headers.get("content-type", ""):
                raise HttpError(415, "Content-Type must be application/json")
            origin = req.headers.get("origin")
            if origin and urlparse(origin).netloc != req.headers.get("host"):
                raise HttpError(403, "Cross-origin request refused")
        if needs_auth:
            req.user = auth.current_user(req.headers)
            if not req.user:
                raise HttpError(401, "Sign in required")
        result = fn(req)
        if isinstance(result, tuple):
            return result
        return _json(200, result)
    except HttpError as e:
        return _json(e.status, {"error": e.msg})
    except policy.Forbidden as e:
        return _json(403, {"error": str(e)})
    except (ValueError, scoring.ConfigInvalid) as e:
        return _json(400, {"error": str(e)})
    except auth.ConfigError as e:
        return _json(503, {"error": "Server not configured: " + str(e)})


# ------------------------------------------------------------------ helpers

def _target(req, key="id"):
    tid = req.query.get(key) if req.method == "GET" else req.json().get(key)
    if not tid:
        raise ValueError("Missing '%s'" % key)
    policy.require_view(req.user, tid)
    return store.user(tid)


def _public_user(u):
    return {"id": u["id"], "name": u["name"], "title": u["title"], "dept": u.get("dept"), "level": u["level"]}


def _employee_scope(actor, include_self=False):
    ids = policy.visible_ids(actor)
    if not include_self:
        ids.discard(actor["id"])
    return [u for u in store.employees() if u["id"] in ids]


def _cards(users):
    return {u["id"]: scoring.scorecard(u) for u in users}


# ------------------------------------------------------------------ auth

@route("GET", "/api/personas", auth_required=False)
def personas(req):
    feat = [_public_user(store.user(i)) for i in FEATURED]
    emps = sorted((_public_user(u) for u in store.employees() if u["level"] == "employee"), key=lambda x: (x["dept"], x["name"]))
    return {"featured": feat, "employees": emps, "access_code_hint": None if auth.is_production() else "Local dev access code: glocomp-demo"}


@route("POST", "/api/login", auth_required=False)
def login(req):
    if not auth.throttle_ok(req.ip):
        raise HttpError(429, "Too many attempts — wait a few minutes")
    body = req.json()
    auth.record_attempt(req.ip)
    u = store.user(body.get("user_id") or "")
    if not u or not auth.check_code(body.get("access_code")):
        raise HttpError(401, "Invalid access code or user")
    store.audit(u, "auth.login", u["id"], {})
    return _json(200, {"ok": True, "level": u["level"]}, [("Set-Cookie", auth.cookie_header(auth.issue(u["id"]), req.secure))])


@route("POST", "/api/logout", auth_required=False)
def logout(req):
    return _json(200, {"ok": True}, [("Set-Cookie", auth.clear_cookie_header(req.secure))])


@route("GET", "/api/session")
def session(req):
    u = req.user
    mgr = store.user(u.get("manager_id")) if u.get("manager_id") else None
    return {"user": dict(_public_user(u), scope_depts=u.get("scope_depts", []), manager=_public_user(mgr) if mgr else None),
            "nav": policy.nav_for(u), "period": store.seed()["meta"]["period"],
            "can": {"see_compensation": policy.can_see_compensation(u), "see_aggregate_cost": policy.can_see_aggregate_cost(u),
                    "see_individual_survey": policy.can_see_individual_survey(u), "edit_role_templates": bool(policy.editable_role_keys(u)),
                    "manage_people": u["level"] != "employee"}}


@route("GET", "/api/access-matrix")
def access_matrix(req):
    return {"levels": ["Director", "Senior Manager", "Manager", "Employee"], "rows": [list(r) for r in policy.ACCESS_MATRIX],
            "your_level": req.user["level"]}


# ------------------------------------------------------------------ performance

@route("GET", "/api/me")
def me(req):
    u = req.user
    if u["level"] == "director":
        raise HttpError(404, "The Director is not scored in this prototype")
    card = scoring.scorecard(u)
    a = hr.assess(u, card, include_individual_survey=False)
    # Employees see their own improvement focus (gap, KPI, suggested action) — never case stage, notes or escalation.
    focus = None
    if a:
        focus = [{"name": k["name"], "attainment_pct": k["attainment_pct"], "points_lost": k["points_lost"],
                  "suggested_action": k["suggested_action"]} for k in a["affected_kpis"]]
    mgr = store.user(u.get("manager_id"))
    card["agreed_with"] = mgr["name"] if mgr else None
    card["improvement_focus"] = focus
    card["library"] = scoring.library(u["dept"])
    return card


@route("GET", "/api/scorecard")
def scorecard(req):
    t = _target(req)
    card = scoring.scorecard(t)
    if policy.manages(req.user, t["id"]):
        card["assessment"] = hr.assess(t, card, include_individual_survey=policy.can_see_individual_survey(req.user))
    return card


@route("GET", "/api/team")
def team(req):
    if req.user["level"] == "employee":
        raise policy.Forbidden("Managers only")
    people = _employee_scope(req.user)
    cards = _cards(people)
    rows = [scoring.summary(c) for c in cards.values()]
    by_dept = {}
    for c in cards.values():
        by_dept.setdefault(c["employee"]["dept"], []).append(c)
    depts = [{"dept": d, "headcount": len(cs), "avg_overall": round(statistics.mean(c["overall"] for c in cs), 1),
              "avg_financial_pct": round(statistics.mean(c["buckets"]["financial"]["attainment_pct"] or 0 for c in cs), 1),
              "avg_non_financial_pct": round(statistics.mean(c["buckets"]["non_financial"]["attainment_pct"] or 0 for c in cs), 1),
              "below_70": sum(1 for c in cs if c["overall"] < 70)} for d, cs in sorted(by_dept.items())]
    return {"people": sorted(rows, key=lambda r: r["overall"]), "departments": depts,
            "survey_themes": hr.team_themes([p["id"] for p in people])}


# ------------------------------------------------------------------ director / senior manager finance

@route("GET", "/api/company")
def company(req):
    if not policy.can_see_company(req.user):
        raise policy.Forbidden("Director only")
    s = store.seed()
    pay_eq = sum(1 for u in store.employees() if any(f["id"] == "pay-equity" for f in hr.pay_flags(u["id"])))
    vol = sum(1 for u in store.employees() if any(f["id"] == "income-volatility" for f in hr.pay_flags(u["id"])))
    commission_n = sum(1 for u in store.employees() if s["compensation"][u["id"]]["pay_structure"] == "Commission")
    agg = roi.aggregate([u["id"] for u in store.employees()])
    flags = [
        {"who": "Pay equity — company-wide", "why": "%d of %d employees sit more than 10%% below their own role's median base salary" % (pay_eq, len(store.employees())), "action": "Open Individuals for the named list", "kind": "warning"},
        {"who": "Income volatility — commission staff", "why": "%d of %d commission-paid employees have over 35%% of OTE in variable pay" % (vol, commission_n), "action": "Open Individuals for the named list", "kind": "warning"},
    ] + [{"who": p["name"], "why": p["flag"], "action": "Review pricing / scope and escalate collections", "kind": "critical"} for p in s["projects"] if p["flag"]]
    return {"series": {k: s["company"][k] for k in ("monthly", "quarterly", "annual")},
            "quarter_cost_base": agg, "flags": flags, "projects": s["projects"],
            "headcount": {"total": len(store.employees()), "commission": commission_n, "bonus": len(store.employees()) - commission_n},
            "attrition_by_division": s["hr_context"]["attrition_by_division"]}


@route("GET", "/api/departments")
def departments(req):
    if req.user["level"] not in ("director", "senior_manager"):
        raise policy.Forbidden("Director or senior manager only")
    out = []
    for d in store.seed()["departments"]:
        if d["name"] not in policy.visible_depts(req.user):
            continue
        members = [u for u in store.employees() if u["dept"] == d["name"]]
        cards = _cards(members)
        row = {"name": d["name"], "color": d["color"], "headcount": len(members),
               "avg_kpi": round(statistics.mean(c["overall"] for c in cards.values()), 1)}
        if policy.can_see_aggregate_cost(req.user) and len(members) >= policy.MIN_GROUP:
            agg = roi.aggregate([u["id"] for u in members])
            row.update(ngp=d["ngp_attributed_annual"], cost=agg["total_cost"] * 4, cost_basis="Annualised bottom-up cost base (salary + variable + expenses + other)")
        out.append(row)
    return {"departments": out}


@route("GET", "/api/projects")
def projects(req):
    if req.user["level"] not in ("director", "senior_manager"):
        raise policy.Forbidden("Director or senior manager only")
    depts = set(policy.visible_depts(req.user))
    return {"projects": [p for p in store.seed()["projects"] if any(a[0] in depts for a in p["attribution"])]}


@route("GET", "/api/roi")
def roi_view(req):
    if not policy.can_see_aggregate_cost(req.user):
        raise policy.Forbidden("Director or senior manager only")
    depts = policy.visible_depts(req.user)
    dept_rows = []
    for d in depts:
        ids = [u["id"] for u in store.employees() if u["dept"] == d]
        if len(ids) >= policy.MIN_GROUP:
            dept_rows.append(dict(roi.aggregate(ids), name=d))
    teams = []
    for d in depts:
        dept_ids = {u["id"] for u in store.employees() if u["dept"] == d}
        cand = []
        for m in store.employees():
            if m["level"] in ("manager", "senior_manager") and m["dept"] == d:
                ids = [r["id"] for r in policy.direct_reports(m["id"]) if r["id"] in dept_ids]
                if len(ids) >= policy.MIN_GROUP:
                    cand.append((m, ids))
        # Differencing guard: department total minus the shown team totals must not isolate 1-4 people.
        remainder = len(dept_ids) - sum(len(ids) for _, ids in cand)
        if cand and 0 < remainder < policy.MIN_GROUP:
            continue
        teams += [dict(roi.aggregate(ids), name="%s's team" % m["name"], dept=d) for m, ids in cand]
    resp = {"period": store.seed()["meta"]["period"], "departments": dept_rows, "teams": teams, "min_group": policy.MIN_GROUP,
            "total": roi.aggregate([u["id"] for u in store.employees() if u["dept"] in depts]),
            "individuals": None}
    if policy.can_see_individual_cost(req.user):
        resp["individuals"] = [dict(roi.individual(u["id"]), id=u["id"], name=u["name"], title=u["title"], dept=u["dept"],
                                    pay_structure=store.seed()["compensation"][u["id"]]["pay_structure"]) for u in store.employees()]
    return resp


@route("GET", "/api/individuals")
def individuals(req):
    if not policy.can_see_compensation(req.user):
        raise policy.Forbidden("Director only")
    rows = []
    for u in store.employees():
        comp = store.seed()["compensation"][u["id"]]
        card = scoring.scorecard(u)
        rows.append({"id": u["id"], "name": u["name"], "role": u["title"], "dept": u["dept"], "pay": comp["pay_structure"],
                     "base": comp["base_annual"], "variable": comp["variable_annual"], "ote": comp["ote_annual"],
                     "splitPct": round(comp["variable_annual"] / comp["ote_annual"], 4), "kpi": card["overall"],
                     "flags": hr.pay_flags(u["id"]) + ([{"id": "performance", "label": "Performance", "detail": "Overall KPI score %.0f%%" % card["overall"]}] if card["overall"] < 70 else []),
                     "roi_pct": roi.individual(u["id"])["roi_pct"]})
    return {"individuals": rows}


@route("GET", "/api/expenses")
def expenses(req):
    if not policy.can_see_individual_cost(req.user):
        raise policy.Forbidden("Director only")
    t = _target(req)
    store.audit(req.user, "read.expenses", t["id"], {})
    return {"employee": _public_user(t), "claims": roi.claims_for(t["id"]), "roi": roi.individual(t["id"])}


# ------------------------------------------------------------------ weightage

def _role_rows(actor):
    keys = sorted({store.role_key(u) for u in _employee_scope(actor, include_self=True)})
    rows = []
    for rk in keys:
        dept, title = rk.split("/", 1)
        rows.append({"role_key": rk, "dept": dept, "title": title, "template": store.role_template(rk),
                     "headcount": sum(1 for u in store.employees() if store.role_key(u) == rk),
                     "editable": policy.can_edit_role_template(actor, rk)})
    return rows


@route("GET", "/api/weights")
def weights(req):
    if req.user["level"] == "employee":
        raise policy.Forbidden("Employees can view their own weightage on My Performance")
    people = []
    for u in _employee_scope(req.user):
        cfg = scoring.effective_config(u)
        people.append({"id": u["id"], "name": u["name"], "title": u["title"], "dept": u["dept"], "role_key": store.role_key(u),
                       "financial_weight": cfg["financial_weight"], "kpi_weights": cfg["kpi_weights"], "targets": cfg["targets"],
                       "individual": cfg["individual"], "agreed_by": cfg["agreed_by"], "agreed_at": cfg["agreed_at"],
                       "template_financial": cfg["template"]["financial_weight"], "editable": policy.manages(req.user, u["id"])})
    depts = sorted({u["dept"] for u in _employee_scope(req.user, include_self=True)})
    return {"roles": _role_rows(req.user), "people": people, "library": {d: scoring.library(d) for d in depts},
            "manager_band": policy.fin_band_for(req.user),
            "rules": "Role templates: Director (any role) and Senior Managers (roles in their departments). Individual agreed weightage/targets: the employee's manager, senior manager or director; managers stay within ±%d pts of the role template's financial weight. Employees: view only. Every change requires a reason and is audit-logged." % policy.MANAGER_FIN_BAND}


def _check_employee_change(actor, u, body):
    policy.require_manage(actor, u["id"])
    cfg = scoring.effective_config(u)
    fw = body.get("financial_weight", cfg["financial_weight"])
    kw = body.get("kpi_weights") or cfg["kpi_weights"]
    band = policy.fin_band_for(actor)
    if band is not None and abs(fw - cfg["template"]["financial_weight"]) > band:
        raise policy.Forbidden("Managers can set an individual's financial weight within ±%d pts of the role template (%d%%). Ask your senior manager for larger changes." % (band, cfg["template"]["financial_weight"]))
    scoring.validate_config(u["dept"], fw, kw)
    targets = dict(cfg["targets"])
    for k, v in (body.get("targets") or {}).items():
        if k not in scoring.library(u["dept"]) or not isinstance(v, (int, float)) or v <= 0:
            raise ValueError("Invalid target for %s" % k)
        targets[k] = v
    return cfg, fw, kw, targets


@route("POST", "/api/weights/preview")
def weights_preview(req):
    body = req.json()
    affected = []
    if body.get("role_key"):
        rk = body["role_key"]
        if not policy.can_edit_role_template(req.user, rk):
            raise policy.Forbidden("You cannot change this role's template")
        fw, kw = body.get("financial_weight"), body.get("kpi_weights")
        scoring.validate_config(rk.split("/", 1)[0], fw, kw)
        for u in store.employees():
            if store.role_key(u) != rk:
                continue
            before = scoring.effective_config(u)
            after = dict(before)
            if not before["individual"]:  # individuals with an agreed override keep it
                after.update(financial_weight=fw, kpi_weights=kw)
            affected.append({"id": u["id"], "name": u["name"] if policy.can_view(req.user, u["id"]) else "(outside your scope)",
                             "before": scoring.scorecard(u, before)["overall"], "after": scoring.scorecard(u, after)["overall"],
                             "keeps_individual_agreement": before["individual"]})
    else:
        u = _target(req, "employee_id")
        before, fw, kw, targets = _check_employee_change(req.user, u, body)
        after = dict(before, financial_weight=fw, kpi_weights=kw, targets=targets)
        card = scoring.scorecard(u, after)
        affected.append({"id": u["id"], "name": u["name"], "before": scoring.scorecard(u, before)["overall"], "after": card["overall"],
                         "buckets": card["buckets"]})
    return {"affected": affected}


@route("POST", "/api/weights/role")
def weights_role(req):
    body = req.json()
    rk = body.get("role_key", "")
    if not policy.can_edit_role_template(req.user, rk) or rk not in store.seed()["role_templates"]:
        raise policy.Forbidden("You cannot change this role's template")
    reason = (body.get("reason") or "").strip()
    if len(reason) < 5:
        raise ValueError("Please give a reason for the change (audit trail)")
    fw, kw = body.get("financial_weight"), body.get("kpi_weights")
    scoring.validate_config(rk.split("/", 1)[0], fw, kw)
    old = store.role_template(rk)
    store.set_role_template(rk, {"financial_weight": fw, "kpi_weights": kw})
    store.audit(req.user, "weights.role", rk, {"from": old, "to": {"financial_weight": fw, "kpi_weights": kw}, "reason": reason[:500]})
    return {"ok": True, "template": store.role_template(rk)}


@route("POST", "/api/weights/employee")
def weights_employee(req):
    body = req.json()
    u = _target(req, "employee_id")
    if body.get("reset"):
        policy.require_manage(req.user, u["id"])
        store.set_employee_override(u["id"], {})
        store.audit(req.user, "weights.employee.reset", u["id"], {})
        return {"ok": True}
    reason = (body.get("reason") or "").strip()
    if len(reason) < 5:
        raise ValueError("Please record what was agreed with the employee (audit trail)")
    cfg, fw, kw, targets = _check_employee_change(req.user, u, body)
    ov = store.employee_override(u["id"])
    ov.update(financial_weight=fw, kpi_weights=kw, targets=targets, kpi_sources=cfg["kpi_sources"],
              agreed_by=req.user["name"], agreed_at=store.now_iso(), reason=reason[:500])
    store.set_employee_override(u["id"], ov)
    store.audit(req.user, "weights.employee", u["id"], {"financial_weight": fw, "reason": reason[:500]})
    return {"ok": True, "scorecard": scoring.summary(scoring.scorecard(u))}


# ------------------------------------------------------------------ HR intervention

@route("GET", "/api/hr/cases")
def hr_cases(req):
    if req.user["level"] == "employee":
        raise policy.Forbidden("Managers only")
    people = _employee_scope(req.user)
    show_survey = policy.can_see_individual_survey(req.user)
    out = []
    for u in people:
        card = scoring.scorecard(u)
        a = hr.assess(u, card, include_individual_survey=show_survey)
        if not a:
            continue
        case = hr.ensure_case(u, a)
        row = {"employee": _public_user(u), "manager": _public_user(store.user(u["manager_id"])) if u.get("manager_id") else None,
               "summary": scoring.summary(card), "assessment": a, "case": case,
               "stage": hr.stage_meta(case["stage"]), "allowed_actions": hr.allowed_actions(req.user, u, case)}
        if show_survey:
            row["pay_flags"] = hr.pay_flags(u["id"])
        out.append(row)
    order = {s: i for i, s in enumerate(hr.STAGE_IDS)}
    out.sort(key=lambda r: (order[r["case"]["stage"]] == order["closed"], -order[r["case"]["stage"]], r["summary"]["overall"]))
    return {"cases": out, "stages": hr.STAGES, "decisions": hr.FINAL_DECISIONS,
            "team_themes": hr.team_themes([p["id"] for p in people]), "individual_survey_visible": show_survey}


@route("POST", "/api/hr/action")
def hr_action(req):
    body = req.json()
    u = _target(req, "employee_id")
    card = scoring.scorecard(u)
    a = hr.assess(u, card, include_individual_survey=False)
    case = store.hr_case(u["id"]) or (hr.ensure_case(u, a) if a else None)
    if not case:
        raise ValueError("No open case for this employee")
    case = hr.apply_action(req.user, u, case, body.get("action"), body)
    return {"ok": True, "case": case, "stage": hr.stage_meta(case["stage"]), "allowed_actions": hr.allowed_actions(req.user, u, case)}


# ------------------------------------------------------------------ AI-assisted KPIs

@route("GET", "/api/ai/suggestions")
def ai_list(req):
    t = _target(req)
    policy.require_manage(req.user, t["id"])
    return {"employee": _public_user(t), "suggestions": store.suggestions(t["id"]), "engine": ai_kpi.active_engine(),
            "scorecard": scoring.summary(scoring.scorecard(t))}


@route("POST", "/api/ai/generate")
def ai_generate(req):
    t = _target(req, "employee_id")
    policy.require_manage(req.user, t["id"])
    items, meta = ai_kpi.generate_with_meta(t, scoring.scorecard(t))
    kept = [s for s in store.suggestions(t["id"]) if s["status"] == "applied"]
    store.set_suggestions(t["id"], kept + items)
    store.audit(req.user, "ai.generate", t["id"], {"count": len(items), "engine": meta["engine"], "fallback": meta["fallback"]})
    return {"suggestions": kept + items, "engine": meta["engine"], "fallback": meta["fallback"]}


@route("POST", "/api/ai/decide")
def ai_decide(req):
    body = req.json()
    t = _target(req, "employee_id")
    policy.require_manage(req.user, t["id"])
    items = store.suggestions(t["id"])
    s = next((x for x in items if x["id"] == body.get("suggestion_id")), None)
    if not s or s["status"] == "applied":
        raise ValueError("Suggestion not found or already applied")
    decision = body.get("decision")
    if decision not in ("accept", "reject", "reopen"):
        raise ValueError("decision must be accept, reject or reopen")
    if "suggested_target" in body:
        v = body["suggested_target"]
        if not isinstance(v, (int, float)) or v <= 0:
            raise ValueError("Target must be a positive number")
        s["edited"] = s["edited"] or v != s["suggested_target"]
        s["suggested_target"] = v
    if "suggested_weight" in body and s["type"] != "retarget":
        w = body["suggested_weight"]
        if not isinstance(w, int) or not 1 <= w <= 70:
            raise ValueError("Weight must be a whole number from 1 to 70 (% of its bucket)")
        s["edited"] = s["edited"] or w != s["suggested_weight"]
        s["suggested_weight"] = w
    s["status"] = {"accept": "accepted", "reject": "rejected", "reopen": "pending_review"}[decision]
    s["decided_by"] = req.user["name"]
    s["manager_note"] = (body.get("note") or "")[:500]
    store.set_suggestions(t["id"], items)
    store.audit(req.user, "ai." + decision, t["id"], {"suggestion": s["id"], "kpi": s["kpi"], "edited": s["edited"]})
    return {"suggestion": s}


@route("POST", "/api/ai/apply")
def ai_apply(req):
    t = _target(req, "employee_id")
    policy.require_manage(req.user, t["id"])
    items = store.suggestions(t["id"])
    before = scoring.scorecard(t)["overall"]
    applied = ai_kpi.apply_accepted(req.user, t, items)
    store.set_suggestions(t["id"], items)
    after = scoring.scorecard(t)
    store.audit(req.user, "ai.apply", t["id"], {"applied": applied})
    return {"applied": applied, "before": before, "after": after["overall"], "scorecard": scoring.summary(after), "suggestions": items}


# ------------------------------------------------------------------ audit

@route("GET", "/api/audit")
def audit_log(req):
    u = req.user
    if u["level"] == "employee":
        raise policy.Forbidden("Not available")
    ids = policy.visible_ids(u)
    entries = store.state()["audit"]
    if u["level"] == "manager":
        entries = [e for e in entries if e["actor"] == u["id"]]
    elif u["level"] == "senior_manager":
        entries = [e for e in entries if e["target"] in ids or e["actor"] == u["id"] or e["target"].split("/")[0] in u["scope_depts"]]
    names = {x["id"]: x["name"] for x in store.users()}
    return {"entries": [dict(e, actor_name=names.get(e["actor"]), target_name=names.get(e["target"], e["target"])) for e in entries[-200:]][::-1]}
