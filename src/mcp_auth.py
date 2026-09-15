"""OAuth 2.1 authorization server for the /mcp/ endpoint, gated by Google Workspace login.

MCP clients (claude.ai, Claude Code, Inspector, ...) speak standard MCP auth to us:
discovery at /.well-known/*, dynamic client registration at /register, PKCE
authorization-code flow at /authorize + /token, refresh + revoke. We never hand
Google tokens to the client; instead /authorize bounces the *user* to Google, and
only when Google confirms a verified account in an allowed Workspace domain
(MCP_ALLOWED_GOOGLE_DOMAINS, default aimtechnologies.co) do we mint our own opaque
access/refresh tokens for the MCP client.

    MCP client --/authorize--> us --302--> Google login --/oauth/google/callback--> us
        --302--> /oauth/consent (one click) --302--> client redirect_uri?code=...
    MCP client --/token (code + PKCE)--> us --> {access_token, refresh_token}

State lives in one JSON file under shared/ (a persisted volume) so registered
clients and refresh tokens survive container restarts. Tokens and codes are stored
as SHA-256 hashes; client secrets must stay in clear text because the SDK's
ClientAuthenticator compares them.

Enabled when GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET are set (see
config.Settings.mcp_oauth_enabled); otherwise mcp_app.py falls back to the legacy
static MCP_AUTH_TOKEN.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import anyio.to_thread
import httpx
import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from config import settings

logger = logging.getLogger("survey_intel")

SCOPE = "mcp"  # the single scope we issue; tokens are all-or-nothing

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

PENDING_TTL = 10 * 60   # seconds a login attempt may take (authorize -> consent)
CODE_TTL = 5 * 60       # authorization code lifetime (RFC 6749 §4.1.2 recommends <=10 min)


class LoginError(Exception):
    """A Google-login / consent step failed. `redirect_url`, when set, sends the
    user back to the MCP client with a standard OAuth error; otherwise the route
    renders the message directly."""

    def __init__(self, message: str, redirect_url: str | None = None, status: int = 400):
        super().__init__(message)
        self.message = message
        self.redirect_url = redirect_url
        self.status = status


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def _email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


# --------------------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------------------

class OAuthStore:
    """All OAuth state in one JSON file; every mutation prunes expired entries and
    rewrites the file atomically. Small enough (tens of clients, a few hundred
    tokens) that this is simpler and safer than a second database."""

    _SECTIONS = ("clients", "pending", "codes", "access", "refresh")

    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = {s: {} for s in self._SECTIONS}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except ValueError:
            logger.error("MCP OAuth store %s is corrupt — starting empty (clients must re-register)", self.path)
            return
        for s in self._SECTIONS:
            if isinstance(raw.get(s), dict):
                self._data[s] = raw[s]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def _prune(self) -> None:
        now = time.time()
        for s in ("pending", "codes", "access", "refresh"):
            sec = self._data[s]
            for k in [k for k, v in sec.items() if v.get("expires_at") and v["expires_at"] < now]:
                del sec[k]

    def get(self, section: str, key: str) -> dict | None:
        with self._lock:
            v = self._data[section].get(key)
            if v and v.get("expires_at") and v["expires_at"] < time.time():
                return None
            return dict(v) if v else None

    def put(self, section: str, key: str, value: dict) -> None:
        with self._lock:
            self._data[section][key] = value
            self._prune()
            self._save()

    def delete(self, section: str, *keys: str) -> None:
        with self._lock:
            changed = False
            for k in keys:
                if k in self._data[section]:
                    del self._data[section][k]
                    changed = True
            if changed:
                self._prune()
                self._save()

    def count(self, section: str) -> int:
        with self._lock:
            return len(self._data[section])


# --------------------------------------------------------------------------------------
# Google OpenID Connect (the upstream identity provider)
# --------------------------------------------------------------------------------------

class GoogleOIDC:
    """Authorization-code login against Google; returns verified ID-token claims."""

    def __init__(self, client_id: str, client_secret: str, callback_url: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = callback_url
        self._jwks = PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True, lifespan=3600)

    def login_url(self, state: str, nonce: str, hd: str | None) -> str:
        q = {
            "client_id": self.client_id,
            "redirect_uri": self.callback_url,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "access_type": "online",
            "prompt": "select_account",
        }
        if hd:  # UI hint only (pre-selects Workspace accounts); enforced later via claims
            q["hd"] = hd
        return f"{GOOGLE_AUTH_URL}?{urlencode(q)}"

    async def login(self, code: str, expected_nonce: str) -> dict[str, Any]:
        """Exchange the Google code and verify the ID token. Raises LoginError."""
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                r = await http.post(GOOGLE_TOKEN_URL, data={
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": self.callback_url,
                    "grant_type": "authorization_code",
                })
        except httpx.HTTPError as e:
            raise LoginError(f"Google token endpoint unreachable: {e}", status=502)
        if r.status_code != 200:
            logger.warning("Google code exchange failed: %s %s", r.status_code, r.text[:300])
            raise LoginError("Google rejected the login code (expired or reused). Please try again.")
        id_token = r.json().get("id_token")
        if not id_token:
            raise LoginError("Google did not return an ID token.")
        try:
            claims = await anyio.to_thread.run_sync(self._decode, id_token)  # JWKS fetch may block
        except jwt.PyJWTError as e:
            logger.warning("Google ID token invalid: %s", e)
            raise LoginError("Google ID token failed verification.")
        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise LoginError("Google ID token has an unexpected issuer.")
        if claims.get("nonce") != expected_nonce:
            raise LoginError("Login nonce mismatch — please restart the connection.")
        return claims

    def _decode(self, id_token: str) -> dict[str, Any]:
        key = self._jwks.get_signing_key_from_jwt(id_token).key
        return jwt.decode(id_token, key, algorithms=["RS256"], audience=self.client_id, leeway=60)


# --------------------------------------------------------------------------------------
# the provider the MCP SDK drives
# --------------------------------------------------------------------------------------

class GoogleWorkspaceProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):

    def __init__(self, store: OAuthStore, google: GoogleOIDC, allowed_domains: tuple[str, ...],
                 base_url: str, access_ttl: int, refresh_ttl: int):
        self.store = store
        self.google = google
        self.allowed_domains = tuple(d.lower() for d in allowed_domains)
        self.base_url = base_url.rstrip("/")
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl

    # ---- clients (RFC 7591 dynamic registration) ---------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        rec = self.store.get("clients", client_id)
        return OAuthClientInformationFull.model_validate(rec) if rec else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            p = urlparse(str(uri))
            is_loopback = p.hostname in ("localhost", "127.0.0.1", "::1")
            if p.scheme != "https" and not (p.scheme == "http" and is_loopback):
                raise RegistrationError("invalid_redirect_uri",
                                        f"redirect_uri must be https (or http on localhost): {uri}")
        assert client_info.client_id
        self.store.put("clients", client_info.client_id, client_info.model_dump(mode="json"))
        logger.info("MCP OAuth: registered client %s (%s)", client_info.client_id, client_info.client_name)

    # ---- /authorize -> Google -----------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        pid, nonce = _new_token(), _new_token()
        self.store.put("pending", pid, {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "state": params.state,
            "scopes": params.scopes or [SCOPE],
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": params.resource,
            "google_nonce": nonce,
            "user": None,
            "consent_nonce": None,
            "expires_at": time.time() + PENDING_TTL,
        })
        hd = self.allowed_domains[0] if len(self.allowed_domains) == 1 else None
        return self.google.login_url(state=pid, nonce=nonce, hd=hd)

    def _client_error_url(self, pending: dict, error: str, description: str) -> str:
        return construct_redirect_uri(pending["redirect_uri"], error=error,
                                      error_description=description, state=pending.get("state"))

    async def complete_google_login(self, state: str | None, code: str | None,
                                    google_error: str | None) -> str:
        """Handle Google's redirect back to us. Returns the URL to send the browser to
        next (our consent page). Raises LoginError otherwise."""
        pending = self.store.get("pending", state or "")
        if not pending:
            raise LoginError("This login link has expired or was already used. "
                             "Go back to your MCP client and connect again.")
        if google_error or not code:
            self.store.delete("pending", state)
            raise LoginError("Google login was cancelled.",
                             self._client_error_url(pending, "access_denied", f"google: {google_error or 'no code'}"))
        try:
            claims = await self.google.login(code, expected_nonce=pending["google_nonce"])
        except LoginError as e:
            self.store.delete("pending", state)
            raise LoginError(e.message, self._client_error_url(pending, "server_error", e.message), e.status)

        email = (claims.get("email") or "").lower()
        hd = (claims.get("hd") or "").lower()
        ok = (bool(claims.get("email_verified")) and _email_domain(email) in self.allowed_domains
              and hd in self.allowed_domains)
        if not ok:
            self.store.delete("pending", state)
            logger.warning("MCP OAuth: rejected Google account %s (hd=%r)", email or "?", hd)
            msg = (f"{email or 'This account'} is not a {' / '.join(self.allowed_domains)} Google Workspace "
                   f"account. Sign in with your work Google account.")
            raise LoginError(msg, self._client_error_url(pending, "access_denied", msg), 403)

        pending["user"] = {"email": email, "sub": claims.get("sub"), "name": claims.get("name"),
                           "picture": claims.get("picture")}
        pending["consent_nonce"] = _new_token()
        self.store.put("pending", state, pending)
        return f"{self.base_url}/oauth/consent?{urlencode({'p': state})}"

    # ---- consent page -------------------------------------------------------------

    def consent_context(self, pid: str) -> dict:
        pending = self.store.get("pending", pid or "")
        if not pending or not pending.get("user"):
            raise LoginError("This login link has expired or was already used. "
                             "Go back to your MCP client and connect again.")
        user = pending["user"]
        picture = user.get("picture") or ""
        return {
            "pid": pid,
            "nonce": pending["consent_nonce"],
            "email": user["email"],
            "name": user.get("name") or user["email"].split("@")[0],
            # only Google-hosted avatars are rendered (claims come from a verified ID token)
            "picture": picture if picture.startswith("https://lh3.googleusercontent.com/") else "",
            "client_name": pending.get("client_name") or "an MCP client",
            "redirect_host": urlparse(pending["redirect_uri"]).netloc,
        }

    def finish_consent(self, pid: str, nonce: str, approved: bool) -> str:
        """Mint the authorization code (or deny) and return the client redirect URL."""
        pending = self.store.get("pending", pid or "")
        if not pending or not pending.get("user") or not nonce \
                or not secrets.compare_digest(nonce, pending["consent_nonce"] or ""):
            raise LoginError("This consent form has expired. Go back to your MCP client and connect again.")
        self.store.delete("pending", pid)
        if not approved:
            return self._client_error_url(pending, "access_denied", "user denied access")
        code = _new_token()
        self.store.put("codes", _h(code), {
            "client_id": pending["client_id"],
            "scopes": pending["scopes"],
            "code_challenge": pending["code_challenge"],
            "redirect_uri": pending["redirect_uri"],
            "redirect_uri_provided_explicitly": pending["redirect_uri_provided_explicitly"],
            "resource": pending.get("resource"),
            "subject": pending["user"]["email"],
            "expires_at": time.time() + CODE_TTL,
        })
        logger.info("MCP OAuth: %s authorized client %s", pending["user"]["email"], pending["client_id"])
        return construct_redirect_uri(pending["redirect_uri"], code=code, state=pending.get("state"))

    # ---- /token -----------------------------------------------------------------------

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        rec = self.store.get("codes", _h(authorization_code))
        if not rec:
            return None
        return AuthorizationCode(code=authorization_code, redirect_uri=AnyUrl(rec["redirect_uri"]),
                                 **{k: v for k, v in rec.items() if k != "redirect_uri"})

    def _issue(self, client_id: str, scopes: list[str], subject: str, resource: str | None) -> OAuthToken:
        access, refresh = _new_token(), _new_token()
        now = int(time.time())
        self.store.put("access", _h(access), {
            "client_id": client_id, "scopes": scopes, "subject": subject, "resource": resource,
            "expires_at": now + self.access_ttl, "refresh": _h(refresh),
        })
        self.store.put("refresh", _h(refresh), {
            "client_id": client_id, "scopes": scopes, "subject": subject, "resource": resource,
            "expires_at": now + self.refresh_ttl, "access": _h(access),
        })
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=self.access_ttl,
                          scope=" ".join(scopes), refresh_token=refresh)

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        key = _h(authorization_code.code)
        rec = self.store.get("codes", key)
        if not rec:  # replay (SDK already checked expiry/PKCE/redirect)
            raise TokenError("invalid_grant", "authorization code already used")
        self.store.delete("codes", key)
        return self._issue(rec["client_id"], rec["scopes"], rec["subject"], rec.get("resource"))

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str) -> RefreshToken | None:
        rec = self.store.get("refresh", _h(refresh_token))
        if not rec:
            return None
        return RefreshToken(token=refresh_token, client_id=rec["client_id"], scopes=rec["scopes"],
                            expires_at=int(rec["expires_at"]), subject=rec.get("subject"))

    async def exchange_refresh_token(self, client: OAuthClientInformationFull,
                                     refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        key = _h(refresh_token.token)
        rec = self.store.get("refresh", key)
        if not rec:
            raise TokenError("invalid_grant", "refresh token revoked")
        self.store.delete("refresh", key)          # rotate: old refresh + its access die together
        self.store.delete("access", rec["access"])
        return self._issue(rec["client_id"], scopes or rec["scopes"], rec["subject"], rec.get("resource"))

    # ---- bearer verification on every /mcp/ request ------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        rec = self.store.get("access", _h(token))
        if not rec:
            return None
        return AccessToken(token=token, client_id=rec["client_id"], scopes=rec["scopes"],
                           expires_at=int(rec["expires_at"]), resource=rec.get("resource"),
                           subject=rec.get("subject"),
                           claims={"iss": self.base_url, "email": rec.get("subject")})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        key = _h(token.token)
        if isinstance(token, RefreshToken):
            rec = self.store.get("refresh", key)
            if rec:
                self.store.delete("access", rec["access"])
                self.store.delete("refresh", key)
        else:
            rec = self.store.get("access", key)
            if rec:
                self.store.delete("refresh", rec["refresh"])
                self.store.delete("access", key)


def build_provider() -> GoogleWorkspaceProvider | None:
    """The process-wide provider, or None when Google OAuth is not configured."""
    if not settings.mcp_oauth_enabled:
        return None
    base = settings.base_url
    google = GoogleOIDC(settings.google_oauth_client_id, settings.google_oauth_client_secret,
                        callback_url=f"{base}/oauth/google/callback")
    return GoogleWorkspaceProvider(
        store=OAuthStore(settings.shared_dir / "_mcp_oauth.json"),
        google=google,
        allowed_domains=settings.mcp_allowed_google_domains,
        base_url=base,
        access_ttl=settings.mcp_access_token_ttl,
        refresh_ttl=settings.mcp_refresh_token_ttl,
    )


provider = build_provider()
