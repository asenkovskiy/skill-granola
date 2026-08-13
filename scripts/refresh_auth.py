#!/usr/bin/env python3
"""Re-authenticate the Granola sync skill against Granola 7.5x+ (sealed storage).

Since Granola moved its auth into an entitlement-protected Keychain (see
capture_addon.py for the full explanation), the CLI can no longer read tokens
from local files. This helper captures a fresh WorkOS token pair from the running
Granola app's own HTTPS traffic, through a temporary local mitmproxy, and writes
them to the plaintext supabase.json that granola.py reads. granola.py then keeps
itself alive by refreshing the access token on its own.

What it does (and undoes on exit):
  1. installs mitmproxy via `uv tool` if it is not already present
  2. generates + trusts mitmproxy's CA in the login keychain   -> untrusted at end
  3. points the active network service's HTTP/HTTPS proxy at it -> disabled at end
  4. restarts Granola so it re-authenticates through the proxy
  5. injects one 401 to force a token refresh and captures the pair
  6. writes supabase.json, then tears everything down

Requires: macOS, the Granola desktop app installed and signed in, `uv` on PATH.
A macOS password dialog appears once when trusting the CA — approve it.

Run:  python3 refresh_auth.py
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import base64
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDON = HERE / "capture_addon.py"
GRANOLA_DIR = Path.home() / "Library/Application Support/Granola"
SUPABASE_PATH = GRANOLA_DIR / "supabase.json"
CA_CERT = Path.home() / ".mitmproxy/mitmproxy-ca-cert.pem"
LOGIN_KC = Path.home() / "Library/Keychains/login.keychain-db"
CAPTURE = Path("/tmp/granola-capture.json")
DONE = Path("/tmp/granola-capture.done")
PROXY_HOST, PROXY_PORT = "127.0.0.1", "8080"
GRANOLA_BIN = "/Applications/Granola.app/Contents/MacOS/Granola"


def log(msg):
    print(f"[refresh-auth] {msg}", flush=True)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def find_mitmdump():
    for cand in (shutil.which("mitmdump"), str(Path.home() / ".local/bin/mitmdump")):
        if cand and Path(cand).exists():
            return cand
    log("installing mitmproxy via uv tool ...")
    if run(["uv", "tool", "install", "mitmproxy"]).returncode != 0:
        sys.exit("could not install mitmproxy; install it manually and retry")
    p = str(Path.home() / ".local/bin/mitmdump")
    if not Path(p).exists():
        sys.exit("mitmdump not found after install")
    return p


def active_service():
    """Network service name (e.g. 'Wi-Fi') for the default-route interface."""
    dev = ""
    m = re.search(r"interface:\s*(\S+)", run(["route", "get", "default"]).stdout)
    if m:
        dev = m.group(1)
    order = run(["networksetup", "-listnetworkserviceorder"]).stdout
    # blocks look like: "(1) Wi-Fi\n(Hardware Port: Wi-Fi, Device: en0)"
    for name, d in re.findall(r"\)\s*(.+?)\n\(Hardware Port:.*?, Device:\s*(\S+?)\)", order):
        if d == dev:
            return name.strip()
    return "Wi-Fi"


def set_proxy(service, on):
    st = "on" if on else "off"
    for kind in ("-setsecurewebproxy", "-setwebproxy"):
        if on:
            run(["networksetup", kind, service, PROXY_HOST, PROXY_PORT])
        run(["networksetup", kind + "state", service, "off" if not on else "on"])
    log(f"system proxy {st} on '{service}'")


def kill_granola():
    pids = run(["pgrep", "-f", "Granola.app/Contents/MacOS/Granola"]).stdout.split()
    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except (ProcessLookupError, ValueError):
            pass
    time.sleep(2)


def gen_and_trust_ca(mitmdump):
    if not CA_CERT.exists():
        log("generating mitmproxy CA ...")
        p = subprocess.Popen([mitmdump, "-p", PROXY_PORT],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(20):
            time.sleep(0.5)
            if CA_CERT.exists():
                break
        p.terminate()
        p.wait(timeout=5)
    if not CA_CERT.exists():
        sys.exit("mitmproxy CA was not generated")
    log("trusting CA (approve the macOS password dialog) ...")
    run(["security", "add-trusted-cert", "-r", "trustRoot", "-p", "ssl",
         "-k", str(LOGIN_KC), str(CA_CERT)])


def untrust_ca():
    run(["security", "delete-certificate", "-c", "mitmproxy", str(LOGIN_KC)])
    log("removed mitmproxy CA trust")


def seed_supabase():
    cap = json.loads(CAPTURE.read_text())
    at, rt = cap.get("access_token"), cap.get("refresh_token")
    if not (at and rt):
        sys.exit("capture incomplete: missing access or refresh token")
    p = at.split(".")[1]
    p += "=" * (-len(p) % 4)
    claims = json.loads(base64.urlsafe_b64decode(p))
    iat, exp = claims.get("iat", int(time.time())), claims.get("exp", 0)
    workos = {
        "access_token": at,
        "refresh_token": rt,
        "obtained_at": iat * 1000,
        "expires_in": (exp - iat) if exp and iat else 21600,
        "token_type": "Bearer",
    }
    SUPABASE_PATH.write_text(json.dumps({"workos_tokens": json.dumps(workos)}))
    os.chmod(SUPABASE_PATH, 0o600)
    log(f"wrote {SUPABASE_PATH}")


def main():
    if sys.platform != "darwin":
        sys.exit("macOS only")
    if not Path(GRANOLA_BIN).exists():
        sys.exit("Granola app not found at /Applications/Granola.app")
    if not ADDON.exists():
        sys.exit(f"missing addon: {ADDON}")

    mitmdump = find_mitmdump()
    service = active_service()
    for f in (CAPTURE, DONE):
        f.unlink(missing_ok=True)

    gen_and_trust_ca(mitmdump)
    mitm = None
    try:
        mitm = subprocess.Popen(
            [mitmdump, "-p", PROXY_PORT, "--set", "block_global=false", "-s", str(ADDON)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        set_proxy(service, True)
        log("restarting Granola through the proxy ...")
        kill_granola()
        subprocess.run(["open", "-a", "Granola"])
        log("waiting for token capture (forcing a refresh) ...")
        for i in range(45):
            time.sleep(2)
            if DONE.exists():
                log(f"captured both tokens after {i * 2}s")
                break
        else:
            sys.exit("timed out waiting for tokens; is Granola signed in?")
        # tear down networking BEFORE seeding so the app returns to normal
        set_proxy(service, False)
        seed_supabase()
    finally:
        set_proxy(service, False)
        if mitm:
            mitm.terminate()
            try:
                mitm.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mitm.kill()
        untrust_ca()

    log("done. Verifying sync ...")
    rc = subprocess.run([sys.executable, str(HERE / "granola.py"),
                         "sync", "--since", time.strftime("%Y-%m-%d")]).returncode
    sys.exit(rc)


if __name__ == "__main__":
    main()
