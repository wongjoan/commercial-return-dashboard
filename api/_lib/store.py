"""Data access layer.

* seed.json  — read-only reference data (org, compensation, KPI records, expenses, HR context). Never served directly.
* state file — mutable configuration and workflow data: role weightage templates, per-employee agreed overrides,
  HR cases, AI KPI suggestions and the audit log.

State location: $GLOCOMP_STATE_PATH, else /tmp on Vercel (ephemeral — see GLOCOMP.md "Reliability"), else
data/runtime/state.json locally. The production target is Supabase Postgres (supabase/schema.sql); every read and
write goes through the functions in this module so swapping the backend does not touch the API or policy code.
"""
import copy
import datetime as dt
import json
import os
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
SEED_PATH = os.path.join(_HERE, "data", "seed.json")
_lock = threading.RLock()
_seed = None
_state = None


def _state_path():
    if os.environ.get("GLOCOMP_STATE_PATH"):
        return os.environ["GLOCOMP_STATE_PATH"]
    if os.environ.get("VERCEL"):
        return "/tmp/glocomp_state.json"
    return os.path.normpath(os.path.join(_HERE, "..", "..", "data", "runtime", "state.json"))


def seed():
    global _seed
    if _seed is None:
        with open(SEED_PATH, encoding="utf-8") as f:
            _seed = json.load(f)
        _seed["_users_by_id"] = {u["id"]: u for u in _seed["users"]}
    return _seed


def _empty_state():
    return {"version": 1, "role_templates": {}, "employee_overrides": {}, "hr_cases": {}, "ai_suggestions": {}, "audit": []}


def state():
    global _state
    with _lock:
        if _state is None:
            path = _state_path()
            try:
                with open(path, encoding="utf-8") as f:
                    _state = json.load(f)
            except (OSError, ValueError):
                _state = _empty_state()
        return _state


def save():
    with _lock:
        path = _state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f, indent=1)
        os.replace(tmp, path)  # atomic swap so a crash mid-write never leaves a half-written file


def reset_state():
    """Test/demo helper: discard all runtime changes."""
    global _state
    with _lock:
        _state = _empty_state()
        save()


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def user(uid):
    return seed()["_users_by_id"].get(uid)


def users():
    return [u for u in seed()["users"]]


def employees():
    """Everyone with a commercial role (excludes the Director, who is not scored)."""
    return [u for u in seed()["users"] if u["level"] != "director"]


def role_key(u):
    return "%s/%s" % (u["dept"], u["title"])


def role_template(rk):
    st = state()
    if rk in st["role_templates"]:
        return copy.deepcopy(st["role_templates"][rk])
    return copy.deepcopy(seed()["role_templates"][rk])


def set_role_template(rk, tpl):
    with _lock:
        state()["role_templates"][rk] = tpl
        save()


def employee_override(uid):
    return copy.deepcopy(state()["employee_overrides"].get(uid) or {})


def set_employee_override(uid, ov):
    with _lock:
        state()["employee_overrides"][uid] = ov
        save()


def audit(actor, action, target, detail):
    with _lock:
        state()["audit"].append({"at": now_iso(), "actor": actor["id"], "actor_level": actor["level"],
                                 "action": action, "target": target, "detail": detail})
        state()["audit"] = state()["audit"][-2000:]
        save()


def hr_case(uid):
    return state()["hr_cases"].get(uid)


def set_hr_case(uid, case):
    with _lock:
        state()["hr_cases"][uid] = case
        save()


def suggestions(uid):
    return copy.deepcopy(state()["ai_suggestions"].get(uid) or [])


def set_suggestions(uid, items):
    with _lock:
        state()["ai_suggestions"][uid] = items
        save()
