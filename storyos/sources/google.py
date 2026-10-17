"""Sign in to Google once, then pull Docs into the library.

Story OS runs on the writer's machine, so it authenticates as the writer
rather than holding a service account. The flow is the standard OAuth
installed-app one: open a consent page in the browser, catch the redirect on
localhost, exchange the code, keep the refresh token.

**The tokens are the writer's.** They are stored in the environment directory
with owner-only permissions and are never written to `models.json`, never
logged, and never sent anywhere but Google. `.gitignore` excludes the file, and
`tools/scan_secrets.py` will refuse a commit that contains one anyway.

One piece of setup cannot be avoided: a desktop OAuth client, created once in
the Google Cloud console. Shipping a client secret inside an open-source tool
would mean every user of it shared one identity and one quota, and Google can
revoke it for everyone at once. Five minutes of setup, documented in
`connect()`'s error message, buys an independent one.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"

# drive.readonly is what lets the writer import a document they already have.
# The narrower drive.file scope only reaches files this app itself created,
# which is useless for pulling in notes written years ago.
SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)

# Google Docs exports Markdown directly, which keeps headings, lists, bold and
# links intact. Converting from HTML or plain text loses structure the library
# search and the pin anchors both rely on.
EXPORT_PREFERENCE = ("text/markdown", "text/plain", "text/html")

DOC_ID = re.compile(r"/(?:document|file|spreadsheets|presentation)/d/([A-Za-z0-9_-]{20,})")
BARE_ID = re.compile(r"^[A-Za-z0-9_-]{20,}$")


class NotConnected(Exception):
    """No stored token. The caller should prompt the writer to sign in."""


class SetupNeeded(Exception):
    """No OAuth client configured. Carries the instructions."""


def doc_id(url_or_id: str) -> str:
    """Accept a pasted browser URL or a bare id. Never guesses."""
    s = (url_or_id or "").strip()
    if m := DOC_ID.search(s):
        return m.group(1)
    if BARE_ID.match(s):
        return s
    if q := urllib.parse.parse_qs(urllib.parse.urlparse(s).query).get("id"):
        return q[0]
    raise ValueError(
        f"not a Google Docs link or id: {url_or_id!r}\n"
        f"Paste the URL from the browser address bar, e.g.\n"
        f"  https://docs.google.com/document/d/1AbC.../edit")


# -- credentials ----------------------------------------------------------

def _token_path(env) -> Path:
    return env.root / ".google-token.json"


def client_config(env) -> tuple[str, str]:
    """Client id and secret, from the environment. Never from a project file."""
    cid = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        raise SetupNeeded(SETUP_TEXT)
    return cid, secret


SETUP_TEXT = """\
Story OS needs its own Google OAuth client before it can sign you in. This is
a one-time setup of about five minutes, and the credentials stay on your
machine.

  1. Go to  https://console.cloud.google.com/projectcreate
     Create a project; any name.
  2. Enable the Drive API:
     https://console.cloud.google.com/apis/library/drive.googleapis.com
  3. Configure the consent screen as "External", and add your own Google
     address under Test users. The app stays unverified, which is correct --
     it is yours and has one user.
  4. Credentials -> Create credentials -> OAuth client ID -> Desktop app.
  5. Put the two values in your environment's .env:

       GOOGLE_CLIENT_ID=....apps.googleusercontent.com
       GOOGLE_CLIENT_SECRET=....

Then run `storyos connect google` again.

Why you make your own: a client secret shipped inside the tool would be shared
by everyone who installs it -- one identity, one quota, and one revocation that
breaks it for all of them."""


def load_token(env) -> dict | None:
    p = _token_path(env)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def save_token(env, tok: dict) -> None:
    p = _token_path(env)
    p.write_text(json.dumps(tok, indent=2) + "\n")
    os.chmod(p, 0o600)        # owner only; it is a live credential


def forget(env) -> bool:
    """Sign out. Removes the stored token; Google-side access is revoked at
    myaccount.google.com/permissions, which this cannot and should not do."""
    p = _token_path(env)
    if p.is_file():
        p.unlink()
        return True
    return False


def status(env) -> dict:
    tok = load_token(env)
    if not tok:
        try:
            client_config(env)
            return {"connected": False, "ready_to_connect": True}
        except SetupNeeded:
            return {"connected": False, "ready_to_connect": False}
    return {"connected": True, "ready_to_connect": True,
            "expires_in": int(tok.get("expires_at", 0) - time.time()),
            "scopes": tok.get("scope", "").split()}


# -- the sign-in flow -----------------------------------------------------

class _CatchCode(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None
    error: str | None = None
    expect_state: str = ""

    def do_GET(self):
        args = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        got_state = (args.get("state") or [""])[0]

        if got_state != self.expect_state:
            # A mismatched state means the redirect did not come from the
            # request we started. Refuse it rather than exchange the code.
            _CatchCode.error = "state mismatch - sign-in refused"
            body = _page("Sign-in refused",
                         "That redirect did not match the request Story OS "
                         "started. Nothing was saved. Close this tab and try again.")
        elif "error" in args:
            _CatchCode.error = args["error"][0]
            body = _page("Sign-in cancelled", f"Google said: {args['error'][0]}")
        elif "code" in args:
            _CatchCode.code = args["code"][0]
            body = _page("Connected",
                         "Story OS can now read the Google Docs you choose to "
                         "import. You can close this tab.")
        else:
            body = _page("Nothing to do", "No authorisation code in that request.")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _page(title: str, body: str) -> bytes:
    return (f"""<!doctype html><meta charset=utf-8><title>{title}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:30em;margin:18vh auto;
padding:0 1.5em;color:#1A1815;background:#FAF8F3}}
h1{{font:600 22px/1.3 Georgia,serif;margin:0 0 .5em}}
p{{color:#6B6459}}</style>
<h1>{title}</h1><p>{body}</p>""").encode()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def connect(env, *, open_browser: bool = True, timeout: int = 180) -> dict:
    """Run the sign-in. Returns the stored token record.

    Uses a loopback redirect on a port chosen at call time, which is the flow
    Google documents for installed apps -- no client secret travels over the
    network to a third party, and the authorisation code is bound to a
    one-time `state` this process generated.
    """
    cid, csecret = client_config(env)
    port = _free_port()
    redirect = f"http://127.0.0.1:{port}/"
    state = secrets.token_urlsafe(24)

    _CatchCode.code = _CatchCode.error = None
    _CatchCode.expect_state = state

    params = {
        "client_id": cid,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",       # we want a refresh token
        "prompt": "consent",            # ensure one is actually issued
        "state": state,
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    httpd = http.server.HTTPServer(("127.0.0.1", port), _CatchCode)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print("Opening Google sign-in in your browser.")
    print("If it does not open, paste this into the browser yourself:\n")
    print("  " + url + "\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _CatchCode.code or _CatchCode.error:
            break
        time.sleep(0.25)
    httpd.shutdown()

    if _CatchCode.error:
        raise RuntimeError(f"sign-in failed: {_CatchCode.error}")
    if not _CatchCode.code:
        raise TimeoutError(
            f"no response within {timeout}s. Run `storyos connect google` again.")

    tok = _exchange({"code": _CatchCode.code, "grant_type": "authorization_code",
                     "redirect_uri": redirect}, cid, csecret)
    save_token(env, tok)
    return tok


def _exchange(extra: dict, cid: str, csecret: str) -> dict:
    data = urllib.parse.urlencode(
        {"client_id": cid, "client_secret": csecret, **extra}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data,
                                 headers={"Content-Type":
                                          "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as x:
        detail = x.read().decode(errors="replace")[:300]
        raise RuntimeError(f"Google refused the token request ({x.code}): {detail}") from None

    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    return tok


def access_token(env) -> str:
    """A live access token, refreshing silently when the old one has expired."""
    tok = load_token(env)
    if not tok:
        raise NotConnected(
            "not signed in to Google. Run: storyos connect google")

    if tok.get("expires_at", 0) > time.time():
        return tok["access_token"]

    refresh = tok.get("refresh_token")
    if not refresh:
        raise NotConnected(
            "the stored Google token cannot be refreshed. "
            "Run: storyos connect google")

    cid, csecret = client_config(env)
    fresh = _exchange({"refresh_token": refresh, "grant_type": "refresh_token"},
                      cid, csecret)
    # A refresh response omits the refresh token; keep the one we have.
    fresh.setdefault("refresh_token", refresh)
    save_token(env, fresh)
    return fresh["access_token"]


# -- reading documents ----------------------------------------------------

def _get(url: str, token: str, *, params: dict | None = None) -> tuple[bytes, str]:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as x:
        detail = x.read().decode(errors="replace")[:300]
        if x.code in (401, 403):
            raise NotConnected(
                f"Google refused the request ({x.code}). The document may not be "
                f"yours, or the sign-in has been revoked. {detail}") from None
        raise RuntimeError(f"Google returned {x.code}: {detail}") from None


def metadata(env, file_id: str) -> dict:
    raw, _ = _get(f"{DRIVE_FILES}/{file_id}", access_token(env),
                  params={"fields": "id,name,mimeType,modifiedTime,owners(displayName)"})
    return json.loads(raw)


def fetch(env, url_or_id: str) -> dict:
    """Pull one Google Doc down as Markdown.

    Returns `{id, name, mime, modified, text, exported_as}`. Markdown is tried
    first and the fallbacks are only used if Google refuses it, because a Doc
    exported as plain text loses every heading -- and headings are what the
    library's pin anchors attach to.
    """
    fid = doc_id(url_or_id)
    token = access_token(env)
    meta = json.loads(_get(f"{DRIVE_FILES}/{fid}", token, params={
        "fields": "id,name,mimeType,modifiedTime"})[0])

    mime = meta.get("mimeType", "")
    if mime == "application/vnd.google-apps.document":
        text, used = _export(fid, token)
    elif mime.startswith("application/vnd.google-apps"):
        raise ValueError(
            f"{meta['name']!r} is a {mime.rsplit('.', 1)[-1]}, not a document. "
            f"Story OS imports Google Docs; export other types yourself first.")
    else:
        raw, _ = _get(f"{DRIVE_FILES}/{fid}", token, params={"alt": "media"})
        text, used = raw.decode(errors="replace"), mime

    return {"id": fid, "name": meta.get("name", fid), "mime": mime,
            "modified": meta.get("modifiedTime"), "text": text,
            "exported_as": used}


def _export(fid: str, token: str) -> tuple[str, str]:
    last = None
    for mt in EXPORT_PREFERENCE:
        try:
            raw, _ = _get(f"{DRIVE_FILES}/{fid}/export", token, params={"mimeType": mt})
            return raw.decode(errors="replace"), mt
        except RuntimeError as x:
            last = x                     # this format is not offered; try the next
    raise RuntimeError(f"Google would not export that document: {last}")


def search(env, query: str, *, limit: int = 20) -> list[dict]:
    """Find the writer's Docs by name, so an id never has to be typed."""
    safe = query.replace("'", "\\'")
    raw, _ = _get(DRIVE_FILES, access_token(env), params={
        "q": f"name contains '{safe}' and "
             f"mimeType = 'application/vnd.google-apps.document' and trashed = false",
        "fields": "files(id,name,modifiedTime)",
        "orderBy": "modifiedTime desc",
        "pageSize": limit})
    return json.loads(raw).get("files", [])
