"""weather-agent: stage 4. Okta sign-in, automatic ID-JAG exchanges, then ask questions.

Tokens stay on the server. The browser sees only a session cookie, the answer,
and a trace of what happened (never a token).
"""
import os
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode

import anyio
from flask import Flask, abort, redirect, render_template_string, request, session

import agent_loop
import okta

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

STORE: dict[str, dict] = {}   # server-side sessions: sid -> data


def server_session() -> dict:
    sid = session.get("sid")
    if not sid or sid not in STORE:
        sid = secrets.token_urlsafe(32)
        session["sid"] = sid
        STORE[sid] = {}
    return STORE[sid]


def token_valid(s: dict) -> bool:
    """True if we hold an access token with at least 60 seconds left."""
    claims = s.get("access_claims")
    return bool(claims) and claims["exp"] - time.time() > 60


PAGE = """
<!doctype html><title>weather-agent</title>
<body style="font-family: sans-serif; max-width: 900px; margin: 2rem auto;">
<h2>weather-agent</h2>
{% if error %}<p style="color:#a32d2d;"><b>Error:</b> {{ error }}</p>{% endif %}
{% if c %}
  <p>Signed in as <b>{{ c['sub'] }}</b> &middot; <a href="/logout">Sign out</a></p>
  <table style="border-collapse: collapse; font-size: 14px;" cellpadding="4">
    <tr><td>User (<code>uid</code>)</td><td><code>{{ c['uid'] }}</code></td></tr>
    <tr><td>Agent (<code>cid</code>)</td><td><code>{{ c['cid'] }}</code></td></tr>
    <tr><td>Actor (<code>act.sub</code>)</td><td><code>{{ c.get('act', {}).get('sub', '(none)') }}</code></td></tr>
    <tr><td>Scopes (<code>scp</code>)</td><td><code>{{ c['scp'] | join(', ') }}</code></td></tr>
    <tr><td>Access token expires</td><td>{{ expires }}</td></tr>
  </table>
  <form method="post" action="/ask" style="margin-top: 1.5rem;">
    <input name="q" size="70" placeholder="Should I bring a jacket in Denver today?" value="{{ q or '' }}" required>
    <button>Ask</button>
  </form>
  {% if result %}
    <h3>Answer</h3>
    <div style="white-space: pre-wrap; background:#f4f4f4; padding:1rem;">{{ result.answer }}</div>
    <h3>Tools the gateway exposed</h3>
    <p><code>{{ result.tools | join(', ') or '(none)' }}</code></p>
    <h3>Trace</h3>
    <pre style="background:#f4f4f4; padding:1rem;">{{ result.trace | join('\n') }}</pre>
  {% endif %}
{% else %}
  <p><a href="/login">Sign in with Okta</a></p>
{% endif %}
</body>
"""


@app.route("/")
def home():
    s = server_session()
    c = s.get("access_claims") if token_valid(s) else None
    expires = datetime.fromtimestamp(c["exp"]).strftime("%H:%M:%S") if c else ""
    return render_template_string(PAGE, c=c, expires=expires, q=s.pop("q", None),
                                  result=s.pop("result", None), error=s.pop("error", None))


@app.route("/login")
def login():
    s = server_session()
    s["state"] = secrets.token_urlsafe(32)
    s["nonce"] = secrets.token_urlsafe(32)
    s["verifier"], challenge = okta.pkce_pair()
    params = {
        "client_id": okta.CLIENT_ID,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": okta.REDIRECT_URI,
        "state": s["state"],
        "nonce": s["nonce"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return redirect(f"{okta.ORG['authorization_endpoint']}?{urlencode(params)}")


@app.route("/callback")
def callback():
    s = server_session()
    if "error" in request.args:
        return f"Okta returned an error: {request.args['error']}: {request.args.get('error_description')}", 400
    if not s.get("state") or request.args.get("state") != s.pop("state"):
        abort(400, "state mismatch")

    try:
        # Phase 1: sign-in
        tokens = okta.token_request(okta.ORG["token_endpoint"], {
            "grant_type": "authorization_code",
            "code": request.args["code"],
            "redirect_uri": okta.REDIRECT_URI,
            "code_verifier": s.pop("verifier"),
        })
        okta.verify_id_token(tokens["id_token"], s.pop("nonce"))
        s["id_token"] = tokens["id_token"]            # kept only for logout (id_token_hint)

        # Phase 2: ID token -> ID-JAG (valid 5 minutes, so exchange immediately)
        id_jag = okta.exchange_for_id_jag(tokens["id_token"])["access_token"]

        # Phase 3: ID-JAG -> access token for api://weather-mcp
        resp = okta.exchange_for_access_token(id_jag)
        s["access_claims"] = okta.verify_access_token(resp["access_token"])
        s["access_token"] = resp["access_token"]
    except Exception as e:
        s["error"] = f"Sign-in or token exchange failed: {e}"
        print(f"\n=== Sign-in / exchange FAILED ===\n{e}")
    return redirect("/")


@app.route("/ask", methods=["POST"])
def ask():
    s = server_session()
    if not token_valid(s):
        return redirect("/login")          # expired: sign in again for a fresh chain
    question = request.form["q"].strip()
    s["q"] = question
    try:
        s["result"] = anyio.run(agent_loop.run_agent, question, s["access_token"])
    except Exception as e:
        causes = []

        def unwrap(err):
            if isinstance(err, BaseExceptionGroup):
                for sub in err.exceptions:
                    unwrap(sub)
            else:
                causes.append(f"{type(err).__name__}: {err}")

        unwrap(e)
        s["error"] = "Agent call failed: " + " | ".join(causes)
        import traceback
        traceback.print_exception(e)       # full detail in the terminal
    return redirect("/")


@app.route("/logout")
def logout():
    s = server_session()
    id_token = s.get("id_token")
    STORE.pop(session.pop("sid", None), None)
    if not id_token:
        return redirect("/")
    params = {"id_token_hint": id_token, "post_logout_redirect_uri": "http://localhost:5000/"}
    return redirect(f"{okta.ORG['end_session_endpoint']}?{urlencode(params)}")


if __name__ == "__main__":
    app.run(host="localhost", port=5000, debug=False)
