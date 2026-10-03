"""Minimal LLM client (Python standard library only — no SDK, so the Vercel function stays dependency-free).

Configured entirely by environment variables; with no API key set, `configured()` is False and callers fall back.

    LLM_PROVIDER   "anthropic" (default) or "openai" (any OpenAI-compatible Chat Completions API:
                   OpenAI, Groq, OpenRouter, Google Gemini's OpenAI endpoint, a local Ollama, ...)
    LLM_API_KEY    API key (ANTHROPIC_API_KEY / OPENAI_API_KEY are also accepted)
    LLM_MODEL      model id; default for anthropic: claude-haiku-4-5 (fast + cheap). Required for "openai".
    LLM_BASE_URL   optional; default https://api.anthropic.com or https://api.openai.com/v1
    LLM_TIMEOUT    seconds, default 20 (keep below the Vercel maxDuration in vercel.json)

The key is only read server-side and is never logged or returned to the browser.
"""
import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = {"anthropic": "claude-haiku-4-5", "openai": None}
DEFAULT_BASE = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com/v1"}


class LLMError(Exception):
    pass


def provider():
    return (os.environ.get("LLM_PROVIDER") or "anthropic").strip().lower()


def _key():
    p = provider()
    return (os.environ.get("LLM_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY" if p == "anthropic" else "OPENAI_API_KEY") or "").strip()


def model():
    return (os.environ.get("LLM_MODEL") or DEFAULT_MODEL.get(provider()) or "").strip()


def configured():
    return provider() in DEFAULT_BASE and bool(_key()) and bool(model())


def label():
    return "%s:%s" % (provider(), model()) if configured() else None


def _post(url, headers, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers=dict(headers, **{"content-type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise LLMError("HTTP %d from LLM provider: %s" % (e.code, detail))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError("LLM provider unreachable: %s" % e)
    except ValueError:
        raise LLMError("LLM provider returned non-JSON")


def complete(system, user, max_tokens=2000):
    """Send one system + user prompt; return the model's text reply. Raises LLMError on any failure."""
    if not configured():
        raise LLMError("LLM not configured")
    p, timeout = provider(), float(os.environ.get("LLM_TIMEOUT") or 20)
    base = (os.environ.get("LLM_BASE_URL") or DEFAULT_BASE[p]).rstrip("/")
    if p == "anthropic":
        data = _post(base + "/v1/messages", {"x-api-key": _key(), "anthropic-version": "2023-06-01"},
                     {"model": model(), "max_tokens": max_tokens, "temperature": 0.2, "system": system,
                      "messages": [{"role": "user", "content": user}]}, timeout)
        try:
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError):
            raise LLMError("Unexpected Anthropic response shape")
    data = _post(base + "/chat/completions", {"authorization": "Bearer " + _key()},
                 {"model": model(), "max_tokens": max_tokens, "temperature": 0.2,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, timeout)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("Unexpected OpenAI-compatible response shape")
