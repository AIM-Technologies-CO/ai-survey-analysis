"""HTTP side of the MCP OAuth server (see mcp_auth.py for the logic).

Mounted at the site root by server.py, only when Google OAuth is configured:

  /.well-known/oauth-protected-resource[/mcp]   RFC 9728 — points clients at us as AS
  /.well-known/oauth-authorization-server        RFC 8414 — endpoints + capabilities
  /authorize /token /register /revoke            SDK handlers driving GoogleWorkspaceProvider
  /oauth/google/callback                         Google sends the user back here
  /oauth/consent                                 one-click "connect <client> as <email>"
"""

from __future__ import annotations

import html

from fastapi import APIRouter, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
from mcp.server.auth.routes import cors_middleware, create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl
from starlette.routing import Route

from mcp_auth import SCOPE, GoogleWorkspaceProvider, LoginError

router = APIRouter(tags=["oauth"])

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{title}</title>
<style>
 :root{{--bg:#f3f4f6;--card:#fff;--line:#e5e7eb;--fg:#111827;--muted:#6b7280;--soft:#f9fafb;
   --accent:#2563eb;--accent-fg:#fff;--ok:#059669;--err:#b91c1c;--err-bg:#fef2f2}}
 @media (prefers-color-scheme:dark){{:root{{--bg:#0f1115;--card:#161a22;--line:#262b36;--fg:#e5e7eb;
   --muted:#9ca3af;--soft:#1c212b;--accent:#3b82f6;--ok:#34d399;--err:#fca5a5;--err-bg:#2a1518}}}}
 *{{box-sizing:border-box}}
 body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px 16px;
   background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
 .card{{background:var(--card);border:1px solid var(--line);border-radius:16px;width:100%;max-width:460px;
   box-shadow:0 10px 30px rgba(0,0,0,.08);overflow:hidden}}
 .head{{display:flex;align-items:center;gap:12px;padding:20px 24px;border-bottom:1px solid var(--line)}}
 .logo{{width:38px;height:38px;border-radius:10px;background:var(--accent);color:var(--accent-fg);display:grid;
   place-items:center;font-weight:700;font-size:17px;flex:none}}
 .head b{{display:block;font-size:15px}} .head span{{color:var(--muted);font-size:13px}}
 .body{{padding:22px 24px}}
 h1{{font-size:20px;line-height:1.3;margin:0 0 6px}} h1 em{{font-style:normal;color:var(--accent)}}
 p{{margin:6px 0}} .muted{{color:var(--muted);font-size:13.5px}}
 .who{{display:flex;align-items:center;gap:12px;margin:18px 0;padding:12px 14px;border:1px solid var(--line);
   border-radius:12px;background:var(--soft)}}
 .avatar{{width:40px;height:40px;border-radius:50%;flex:none;background:var(--line);display:grid;place-items:center;
   font-weight:600;color:var(--muted);overflow:hidden}} .avatar img{{width:100%;height:100%;display:block}}
 .who b{{display:block;font-size:14.5px}} .who small{{color:var(--muted);font-size:13px;word-break:break-all}}
 .badge{{margin-left:auto;flex:none;font-size:11.5px;color:var(--ok);border:1px solid currentColor;border-radius:999px;
   padding:2px 8px;white-space:nowrap}}
 ul.perms{{list-style:none;margin:14px 0 4px;padding:0;display:grid;gap:8px}}
 ul.perms li{{display:flex;gap:10px;align-items:flex-start;font-size:14px}}
 ul.perms li::before{{content:"";flex:none;width:18px;height:18px;margin-top:2px;border-radius:50%;
   background:var(--accent);-webkit-mask:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path fill="white" d="M7.6 13.2 4.4 10l-1.1 1.1 4.3 4.3 9-9-1.1-1.1z"/></svg>') center/contain no-repeat;
   mask:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path fill="white" d="M7.6 13.2 4.4 10l-1.1 1.1 4.3 4.3 9-9-1.1-1.1z"/></svg>') center/contain no-repeat}}
 .actions{{display:flex;gap:10px;margin-top:22px}} .actions form{{flex:1}}
 button{{width:100%;padding:11px 14px;border-radius:10px;border:1px solid var(--line);background:var(--card);
   color:var(--fg);cursor:pointer;font:inherit;font-weight:600}}
 button.primary{{background:var(--accent);border-color:var(--accent);color:var(--accent-fg)}}
 button:hover{{filter:brightness(1.05)}}
 .foot{{padding:14px 24px;border-top:1px solid var(--line);color:var(--muted);font-size:12.5px}}
 code{{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--soft);border:1px solid var(--line);
   padding:1px 6px;border-radius:6px}}
 .error{{background:var(--err-bg);color:var(--err);border-radius:10px;padding:12px 14px;margin:12px 0}}
</style></head><body><div class="card">
<div class="head"><div class="logo">S</div><div><b>Survey Synthetic Data</b><span>AIM Technologies · MCP server</span></div></div>
<div class="body">{body}</div>
<div class="foot">{foot}</div>
</div></body></html>"""


def _page(title: str, body: str, foot: str = "", status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=html.escape(title), body=body, foot=foot), status_code=status,
                        headers={"Cache-Control": "no-store"})


def _error(e: LoginError):
    if e.redirect_url:
        return RedirectResponse(e.redirect_url, status_code=302, headers={"Cache-Control": "no-store"})
    body = (f"<h1>Couldn&rsquo;t sign you in</h1><div class='error'>{html.escape(e.message)}</div>"
            "<p class='muted'>Return to the app you were connecting from and start the connection again.</p>")
    return _page("Sign-in failed", body,
                 foot="Only verified Google Workspace accounts from AIM Technologies can connect.", status=e.status)


def _provider(request: Request) -> GoogleWorkspaceProvider:
    return request.app.state.mcp_oauth_provider


@router.get("/oauth/google/callback", include_in_schema=False)
async def google_callback(request: Request, code: str | None = None, state: str | None = None,
                          error: str | None = None):
    try:
        url = await _provider(request).complete_google_login(state, code, error)
    except LoginError as e:
        return _error(e)
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})


@router.get("/oauth/consent", include_in_schema=False)
def consent_page(request: Request, p: str = Query("")):
    try:
        ctx = _provider(request).consent_context(p)
    except LoginError as e:
        return _error(e)
    e = {k: html.escape(str(v)) for k, v in ctx.items()}
    avatar = (f'<img src="{e["picture"]}" alt="" referrerpolicy="no-referrer">' if ctx["picture"]
              else html.escape(ctx["name"][:1].upper()))
    hidden = f'<input type="hidden" name="p" value="{e["pid"]}"><input type="hidden" name="n" value="{e["nonce"]}">'
    body = f"""<h1><em>{e['client_name']}</em> wants to connect to your survey tools</h1>
<p class="muted">Allowing lets it act on your behalf through this MCP server.</p>
<div class="who"><div class="avatar">{avatar}</div>
 <div><b>{e['name']}</b><small>{e['email']}</small></div><span class="badge">Google verified</span></div>
<p style="margin:0 0 4px;font-weight:600;font-size:14px">This connection will be able to:</p>
<ul class="perms">
 <li>Browse surveys and their questions in the research database</li>
 <li>Suggest new questions and preview AI-generated answers</li>
 <li>Run full generation jobs (these cost money and are logged under your name)</li>
</ul>
<div class="actions">
 <form method="post" action="/oauth/consent">{hidden}<input type="hidden" name="action" value="deny">
  <button type="submit">Cancel</button></form>
 <form method="post" action="/oauth/consent">{hidden}<input type="hidden" name="action" value="allow">
  <button type="submit" class="primary" autofocus>Allow</button></form>
</div>"""
    foot = (f"After you allow, you&rsquo;ll return to <code>{e['redirect_host']}</code>. "
            "Only continue if you started this connection yourself. Wrong account? Cancel and sign in again.")
    return _page("Connect to Survey Synthetic Data", body, foot)


@router.post("/oauth/consent", include_in_schema=False)
def consent_submit(request: Request, p: str = Form(""), n: str = Form(""), action: str = Form("deny")):
    try:
        url = _provider(request).finish_consent(p, n, approved=(action == "allow"))
    except LoginError as e:
        return _error(e)
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})


def install(app: FastAPI, provider: GoogleWorkspaceProvider) -> None:
    """Attach the OAuth server (SDK handlers + our Google/consent routes) at the site root."""
    app.state.mcp_oauth_provider = provider
    issuer = AnyHttpUrl(provider.base_url)
    resource = AnyHttpUrl(f"{provider.base_url}/mcp")  # identifier for the /mcp/ endpoint

    routes: list[Route] = create_auth_routes(
        provider=provider,
        issuer_url=issuer,
        service_documentation_url=AnyHttpUrl(f"{provider.base_url}/"),
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        revocation_options=RevocationOptions(enabled=True),
    )
    # RFC 9728 metadata: canonical path-suffixed location plus the root fallback that
    # older clients try; both describe the same resource.
    prm = ProtectedResourceMetadataHandler(ProtectedResourceMetadata(
        resource=resource, authorization_servers=[issuer], scopes_supported=[SCOPE],
        resource_name="Survey Synthetic Data MCP"))
    for path in ("/.well-known/oauth-protected-resource/mcp",
                 "/.well-known/oauth-protected-resource/mcp/",
                 "/.well-known/oauth-protected-resource"):
        routes.append(Route(path, endpoint=cors_middleware(prm.handle, ["GET", "OPTIONS"]),
                            methods=["GET", "OPTIONS"]))
    app.router.routes.extend(routes)
    app.include_router(router)
