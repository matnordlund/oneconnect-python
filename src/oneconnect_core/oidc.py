from __future__ import annotations

from dataclasses import dataclass
import asyncio
import base64
import hashlib
import html
import os
import socket
import webbrowser
from urllib.parse import urlencode, urlparse

import aiohttp
from aiohttp import web
import jwt
from jwt import PyJWKClient


class OIDCError(RuntimeError):
    """Raised when OIDC discovery, token exchange, or id_token validation fails."""
    pass


@dataclass(slots=True)
class OIDCResult:
    id_token: str
    refresh_token: str | None
    url: str | None


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _gen_pkce() -> tuple[str, str]:
    verifier = _base64url(os.urandom(32))
    challenge = _base64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _pick_loopback_host() -> str:
    return "127.0.0.1"


def _find_free_port(start: int = 49215, end: int = 65535, host: str = "127.0.0.1") -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    for port in range(start, end + 1):
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free loopback port available")


# Standard algorithms for OIDC id_token (JWT).
_ID_TOKEN_ALGORITHMS = ["RS256", "ES256", "PS256"]


def _verify_id_token_sync(
    id_token: str,
    jwks_uri: str,
    issuer: str,
    audience: str,
    expected_nonce: str | None,
) -> dict:
    """Verify id_token with JWKS; raises PyJWTError on failure."""
    client = PyJWKClient(jwks_uri, timeout=15)
    signing_key = client.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=_ID_TOKEN_ALGORITHMS,
        audience=audience,
        issuer=issuer,
        options={"verify_aud": True, "verify_iss": True, "verify_exp": True},
    )
    if expected_nonce is not None and claims.get("nonce") != expected_nonce:
        raise jwt.InvalidTokenError("nonce mismatch")
    return claims


async def _verify_id_token(
    id_token: str,
    jwks_uri: str,
    issuer: str,
    audience: str,
    expected_nonce: str | None,
) -> dict:
    """Run sync JWKS fetch + decode in executor to avoid blocking."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: _verify_id_token_sync(
            id_token, jwks_uri, issuer, audience, expected_nonce
        ),
    )


def _require_https(url: str, name: str) -> None:
    parsed = urlparse(url)
    if (parsed.scheme or "").lower() != "https":
        raise OIDCError(f"{name} must use HTTPS, got: {url}")


def _validate_discovery_meta(meta: dict) -> None:
    if not meta.get("jwks_uri"):
        raise OIDCError("Discovery document missing jwks_uri")
    if not meta.get("issuer"):
        raise OIDCError("Discovery document missing issuer")
    _require_https(meta["jwks_uri"], "jwks_uri")


async def discover_provider(session: aiohttp.ClientSession, discovery_endpoint: str) -> dict:
    _require_https(discovery_endpoint, "Discovery endpoint")
    async with session.get(discovery_endpoint.rstrip("/"), timeout=aiohttp.ClientTimeout(total=10)) as resp:
        resp.raise_for_status()
        meta = await resp.json()
    _validate_discovery_meta(meta)
    return meta


# Max time to wait for user to complete browser OIDC flow (seconds).
_OIDC_BROWSER_TIMEOUT = 600


async def start_browser_oidc_flow(session: aiohttp.ClientSession, discovery_endpoint: str, client_id: str, nonce: str | None = None) -> OIDCResult:
    host = _pick_loopback_host()
    port = _find_free_port(host=host)
    redirect_uri = f"http://{host}:{port}/oneconnect/oauth/"

    meta = await discover_provider(session, discovery_endpoint)
    auth_endpoint = meta["authorization_endpoint"]
    token_endpoint = meta["token_endpoint"]

    verifier, challenge = _gen_pkce()
    state = _base64url(os.urandom(16))
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid offline_access",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if nonce and nonce.strip():
        params["nonce"] = nonce.strip()

    result_holder: dict = {}

    async def handle(request: web.Request) -> web.Response:
        q = request.rel_url.query
        error = q.get("error")
        code = q.get("code")
        recv_state = q.get("state")

        status = 200
        html_msg = "Authentication completed. You can close this browser tab."
        meta_refresh = ""

        if error:
            status = 400
            html_msg = q.get("error_description") or error
            result_holder["error"] = html_msg
        elif not code or recv_state != state:
            status = 400
            html_msg = "Missing authorization code or invalid state."
            result_holder["error"] = html_msg
        else:
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
            }
            async with session.post(token_endpoint, data=data, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    result_holder["error"] = f"Token exchange failed ({resp.status}): {body}"
                    status = 400
                    html_msg = "Token exchange failed."
                else:
                    tok = await resp.json()
                    id_token = tok.get("id_token") or ""
                    refresh_token = tok.get("refresh_token")
                    if not id_token:
                        result_holder["error"] = "Token response missing id_token"
                        status = 400
                        html_msg = "Token response missing id_token."
                    else:
                        try:
                            claims = await _verify_id_token(
                                id_token,
                                jwks_uri=meta["jwks_uri"],
                                issuer=meta["issuer"],
                                audience=client_id,
                                expected_nonce=nonce.strip() if nonce and nonce.strip() else None,
                            )
                            clavister_url = claims.get("clavister_url")
                            result_holder.update({
                                "id_token": id_token,
                                "refresh_token": refresh_token,
                                "clavister_url": clavister_url,
                            })
                            if clavister_url:
                                meta_refresh = f"http-equiv='refresh' content='1;url={clavister_url}'"
                                html_msg = "Your single sign-on portal is being prepared."
                        except jwt.PyJWTError as e:
                            result_holder["error"] = f"Invalid id_token: {e}"
                            status = 400
                            html_msg = "Token validation failed."

        html_response = (
            f"<html><head><meta {meta_refresh}></head><body style='font-family:sans-serif'>"
            f"<h2>{html.escape(html_msg)}</h2></body></html>"
        )
        return web.Response(text=html_response, status=status, content_type="text/html")

    app = web.Application()
    app.add_routes([web.get("/oneconnect/oauth/", handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()

    async def wait_for_result() -> None:
        webbrowser.open(auth_endpoint + "?" + urlencode(params))
        while not result_holder:
            await asyncio.sleep(0.05)

    try:
        await asyncio.wait_for(wait_for_result(), timeout=_OIDC_BROWSER_TIMEOUT)
        if "error" in result_holder:
            raise OIDCError(result_holder["error"])
        return OIDCResult(
            id_token=result_holder["id_token"],
            refresh_token=result_holder.get("refresh_token"),
            url=result_holder.get("clavister_url"),
        )
    except asyncio.TimeoutError:
        raise OIDCError(
            "Browser sign-in did not complete in time. Please try again."
        ) from None
    finally:
        await runner.cleanup()
