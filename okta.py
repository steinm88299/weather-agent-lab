"""Okta helpers for weather-agent: client assertion, PKCE, token requests, JWT checks."""
import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from pathlib import Path

import jwt  # PyJWT
import requests
from dotenv import load_dotenv

load_dotenv()

OKTA_ORG = os.environ["OKTA_ORG"].rstrip("/")
CLIENT_ID = os.environ["AGENT_CLIENT_ID"]
KEY_ID = os.environ["AGENT_KEY_ID"]
WEATHER_AS_ISSUER = os.environ["WEATHER_AS_ISSUER"].rstrip("/")
PRIVATE_KEY = Path(os.environ["AGENT_PRIVATE_KEY_PATH"]).expanduser().read_text()
REDIRECT_URI = "http://localhost:5000/callback"


def discover(issuer: str) -> dict:
    """Fetch an authorization server's OIDC metadata (endpoints, issuer, JWKS URI)."""
    resp = requests.get(f"{issuer}/.well-known/openid-configuration", timeout=10)
    resp.raise_for_status()
    return resp.json()


ORG = discover(OKTA_ORG)                  # the org authorization server
ORG_KEYS = jwt.PyJWKClient(ORG["jwks_uri"])  # fetches and caches Okta's signing keys
WEATHER_AS = discover(WEATHER_AS_ISSUER)
WEATHER_KEYS = jwt.PyJWKClient(WEATHER_AS["jwks_uri"])
WEATHER_AUDIENCE = "api://weather-mcp"


def client_assertion(token_endpoint: str) -> str:
    """Short-lived JWT signed with the agent's private key (private_key_jwt)."""
    now = int(time.time())
    claims = {
        "iss": CLIENT_ID,
        "sub": CLIENT_ID,
        "aud": token_endpoint,        # must be the exact endpoint being called
        "iat": now,
        "exp": now + 300,
        "jti": str(uuid.uuid4()),     # unique per request; Okta rejects reuse
    }
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": KEY_ID})


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE with S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def token_request(token_endpoint: str, data: dict) -> dict:
    """POST to a token endpoint, authenticating as weather-agent."""
    body = {
        **data,
        "client_id": CLIENT_ID,
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": client_assertion(token_endpoint),
    }
    resp = requests.post(token_endpoint, data=body, headers={"Accept": "application/json"}, timeout=10)
    if not resp.ok:
        raise RuntimeError(f"{resp.status_code} from {token_endpoint}: {resp.text}")
    return resp.json()


def verify_id_token(id_token: str, nonce: str) -> dict:
    """Check signature, issuer, audience, expiry, and nonce. Returns verified claims."""
    key = ORG_KEYS.get_signing_key_from_jwt(id_token).key
    claims = jwt.decode(id_token, key, algorithms=["RS256"], audience=CLIENT_ID, issuer=ORG["issuer"])
    if claims.get("nonce") != nonce:
        raise ValueError("nonce mismatch")
    return claims


def peek(token: str) -> dict:
    """Decode header and claims WITHOUT verifying. For display and debugging only."""
    return {
        "header": jwt.get_unverified_header(token),
        "claims": jwt.decode(token, options={"verify_signature": False}),
    }


def pretty(obj) -> str:
    return json.dumps(obj, indent=2, default=str)

def exchange_for_id_jag(id_token: str, scope: str = "weather:read") -> dict:
    """Exchange 1 (org AS): user's ID token -> ID-JAG addressed to weather-mcp-as."""
    return token_request(ORG["token_endpoint"], {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "requested_token_type": "urn:ietf:params:oauth:token-type:id-jag",
        "subject_token": id_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
        "audience": WEATHER_AS_ISSUER,   # the target AS's issuer, not its token endpoint
        "scope": scope,
    })

def exchange_for_access_token(id_jag: str, scope: str = "weather:read") -> dict:
    """Exchange 2 (weather-mcp-as): ID-JAG -> access token for the weather API."""
    return token_request(WEATHER_AS["token_endpoint"], {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": id_jag,
        "scope": scope,
    })


def verify_access_token(access_token: str) -> dict:
    """The same checks agentgateway will make: signature, issuer, audience, expiry."""
    key = WEATHER_KEYS.get_signing_key_from_jwt(access_token).key
    return jwt.decode(access_token, key, algorithms=["RS256"],
                      audience=WEATHER_AUDIENCE, issuer=WEATHER_AS["issuer"])