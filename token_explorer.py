"""weather-agent web front end. Stage 1: Okta sign-in only."""
import os
import secrets
from urllib.parse import urlencode

from flask import Flask, abort, redirect, render_template_string, request, session

import okta

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

# Server-side session store. The browser cookie holds only a random session ID;
# tokens stay here, on the server. (In memory: restarting the app signs everyone out.)
STORE: dict[str, dict] = {}


def server_session() -> dict:
    sid = session.get("sid")
    if not sid or sid not in STORE:
        sid = secrets.token_urlsafe(32)
        session["sid"] = sid
        STORE[sid] = {}
    return STORE[sid]


PAGE = """
<!doctype html><title>weather-agent</title>
<body style="font-family: sans-serif; max-width: 900px; margin: 2rem auto;">
<h2>weather-agent</h2>
{% if error %}<p style="color:#a32d2d;"><b>Error:</b> {{ error }}</p>{% endif %}
{% if claims %}
  <p>Signed in as <b>{{ claims.get('email') or claims['sub'] }}</b>
     &middot; <a href="/logout">Sign out</a></p>
  <h3>Verified ID token claims</h3>
  <pre style="background:#f4f4f4; padding:1rem;">{{ claims_json }}</pre>
  <form method="post" action="/exchange1">
    <button>Exchange 1: ID token &rarr; ID-JAG</button>
  </form>
  {% if id_jag %}
    <h3>ID-JAG (decoded, unverified)</h3>
    <pre style="background:#f4f4f4; padding:1rem;">{{ id_jag }}</pre>
  {% endif %}
  <!-- Stage 3: begin -->
  <form method="post" action="/exchange2">
    <button>Exchange 2: ID-JAG &rarr; access token</button>
  </form>
  {% if access %}
    <h3>Access token (verified)</h3>
    <pre style="background:#f4f4f4; padding:1rem;">{{ access }}</pre>
  {% endif %}
  <!-- Stage 3: end -->
{% else %}
  <p><a href="/login">Sign in with Okta</a></p>
{% endif %}
</body>
"""


@app.route("/")
def home():
    s = server_session()
    claims = s.get("id_claims")
    return render_template_string(
        PAGE,
        claims=claims,
        claims_json=okta.pretty(claims) if claims else "",
        id_jag=okta.pretty(s["id_jag_view"]) if s.get("id_jag_view") else "",
        access=okta.pretty(s["access_view"]) if s.get("access_view") else "",   # stage 3
        error=s.pop("error", None),
    )


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

    tokens = okta.token_request(okta.ORG["token_endpoint"], {
        "grant_type": "authorization_code",
        "code": request.args["code"],
        "redirect_uri": okta.REDIRECT_URI,
        "code_verifier": s.pop("verifier"),
    })

    s["id_claims"] = okta.verify_id_token(tokens["id_token"], s.pop("nonce"))
    s["id_token"] = tokens["id_token"]  # kept server-side; input to exchange 1 in stage 2
    print("\n=== ID token (unverified view, for debugging) ===")
    print(okta.pretty(okta.peek(tokens["id_token"])))
    return redirect("/")


@app.route("/exchange1", methods=["POST"])
def exchange1():
    s = server_session()
    if "id_token" not in s:
        return redirect("/")
    try:
        resp = okta.exchange_for_id_jag(s["id_token"])
    except RuntimeError as e:
        print(f"\n=== Exchange 1 FAILED ===\n{e}")
        s["error"] = str(e)
        return redirect("/")

    s["id_jag"] = resp["access_token"]   # RFC 8693 returns the issued token in this field
    s["id_jag_view"] = okta.peek(s["id_jag"])
    meta = {k: v for k, v in resp.items() if k != "access_token"}
    print("\n=== Exchange 1 response metadata ===\n" + okta.pretty(meta))
    print("\n=== ID-JAG (unverified view) ===\n" + okta.pretty(s["id_jag_view"]))
    return redirect("/")

@app.route("/exchange2", methods=["POST"])
def exchange2():
    s = server_session()
    if "id_jag" not in s:
        return redirect("/")
    try:
        resp = okta.exchange_for_access_token(s.pop("id_jag"))   # single use: remove it
        claims = okta.verify_access_token(resp["access_token"])
    except Exception as e:
        print(f"\n=== Exchange 2 FAILED ===\n{e}")
        s["error"] = str(e)
        return redirect("/")

    s["access_token"] = resp["access_token"]
    print(f"\nexport TOKEN={resp['access_token']}\n")   # TEMPORARY: for curl testing
    s["access_view"] = {"header": okta.peek(resp["access_token"])["header"], "claims": claims}
    meta = {k: v for k, v in resp.items() if k != "access_token"}
    print("\n=== Exchange 2 response metadata ===\n" + okta.pretty(meta))
    print("\n=== Access token (verified) ===\n" + okta.pretty(s["access_view"]))
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