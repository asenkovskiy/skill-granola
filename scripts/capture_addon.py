"""mitmproxy addon used by refresh_auth.py to capture Granola's WorkOS tokens.

Granola 7.5x seals its auth: supabase.json.enc is AES-256-GCM, its data key (DEK)
lives only in an entitlement-protected macOS Keychain item (com.granola.app.dek)
that non-Granola binaries cannot read, and the app strips --remote-debugging-port
and --proxy-server on launch. So local decryption and CDP are both dead ends.

This addon instead captures the tokens from the app's own HTTPS traffic while it
is routed through mitmproxy. The app holds a valid ~6h access token in memory and
will not refresh on its own, but its request layer calls invalidateAndRefresh()
on an HTTP 401, which POSTs api.granola.ai/v1/refresh-access-token with the current
{refresh_token} and gets back a fresh {access_token, refresh_token}. So we:
  1. read the access_token from the Authorization: Bearer header (always present),
  2. inject ONE 401 on a normal data call to trigger the refresh flow,
  3. capture the refresh_token from the refresh-access-token request/response.
When both are captured we write /tmp/granola-capture.done and stop injecting.
"""
import json
import base64
from pathlib import Path

from mitmproxy import http

OUT = Path("/tmp/granola-capture.json")
DONE = Path("/tmp/granola-capture.done")
state = {"access_token": None, "refresh_token": None, "client_id": None}
flags = {"injected": 0}
MAX_INJECT = 3

# endpoints we must NOT answer with a fake 401
SKIP = ("refresh-access-token", "authenticate", "check-for-update",
        "latest-mac", "get-groq-token", "amplitude", "/2/httpapi")


def _client_id_from_jwt(tok):
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        iss = json.loads(base64.urlsafe_b64decode(payload)).get("iss", "")
        return iss.split("/")[-1] if iss else None
    except Exception:
        return None


def _save():
    OUT.write_text(json.dumps(state))
    if state["access_token"] and state["refresh_token"]:
        DONE.write_text("1")


def request(flow: http.HTTPFlow):
    if not flow.request.pretty_host.endswith("granola.ai"):
        return
    auth = flow.request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and not state["access_token"]:
        state["access_token"] = auth[len("Bearer "):].strip()
        state["client_id"] = _client_id_from_jwt(state["access_token"])
        _save()
    if "refresh-access-token" in flow.request.path or "authenticate" in flow.request.path:
        try:
            body = json.loads(flow.request.get_text() or "{}")
            rt = body.get("refresh_token") or body.get("input", {}).get("refresh_token")
            if rt:
                state["refresh_token"] = rt
                _save()
        except Exception:
            pass


def response(flow: http.HTTPFlow):
    if not flow.request.pretty_host.endswith("granola.ai"):
        return
    path = flow.request.path

    if "refresh-access-token" in path or "authenticate" in path:
        try:
            body = json.loads(flow.response.get_text() or "{}")
            if body.get("access_token"):
                state["access_token"] = body["access_token"]
                state["client_id"] = _client_id_from_jwt(body["access_token"])
            if body.get("refresh_token"):
                state["refresh_token"] = body["refresh_token"]
            _save()
        except Exception:
            pass
        return

    if (state["access_token"] and not state["refresh_token"]
            and flags["injected"] < MAX_INJECT
            and not any(s in path for s in SKIP)
            and flow.request.method in ("POST", "GET")
            and "Bearer" in flow.request.headers.get("Authorization", "")):
        flags["injected"] += 1
        flow.response = http.Response.make(
            401, b'{"error":"unauthorized"}',
            {"Content-Type": "application/json"},
        )
