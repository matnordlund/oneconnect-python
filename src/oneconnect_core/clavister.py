from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Callable, Optional

import aiohttp

from .configauthxml import Authenticator, ClientEnvironment, ConfigAuthXml, ConfigAuthXmlParameter
from .envinfo import build_client_environment
from .oidc import start_browser_oidc_flow
from .profiles import Profile


@dataclass(slots=True)
class TunnelConfiguration:
    dtls_allowed_cipher_suites: list[str] = field(default_factory=lambda: [
        "OC-DTLS1_2-AES128-GCM",
        "OC-DTLS1_2-AES256-GCM",
    ])
    dtls12_allowed_cipher_suites: list[str] = field(default_factory=lambda: [
        "ECDHE-RSA-AES128-GCM-SHA256",
        "ECDHE-RSA-AES256-GCM-SHA384",
        "AES128-GCM-SHA256",
        "AES256-GCM-SHA384",
    ])
    dtls_pre_master_secret: bytes = field(default_factory=lambda: os.urandom(48))


class ClavisterAuthError(RuntimeError):
    pass


def _x_pad_value(body_bytes: bytes) -> str:
    rem = len(body_bytes) % 64
    pad = 64 - rem if rem != 0 else 64
    return "X" * pad


def build_request_headers(client_env: ClientEnvironment, tunnel_cfg: TunnelConfiguration) -> dict[str, str]:
    ua = f"OneConnect/{client_env.client_version} (Clavister OneConnect VPN)"
    dtls_cs = ":".join(["PSK-NEGOTIATE"] + list(tunnel_cfg.dtls_allowed_cipher_suites))
    dtls12_cs = ":".join(tunnel_cfg.dtls12_allowed_cipher_suites)
    master_secret_hex = tunnel_cfg.dtls_pre_master_secret.hex().upper()
    return {
        "User-Agent": ua,
        "X-CSTP-Version": "1",
        "X-CSTP-Base-MTU": "1500",
        "X-CSTP-Address-Type": "IPv4",
        "X-DTLS-CipherSuite": dtls_cs,
        "X-DTLS12-CipherSuite": dtls12_cs,
        "X-DTLS-Accept-Encoding": "identity",
        "X-DTLS-Master-Secret": master_secret_hex,
    }


async def _post_config_auth(
    session: aiohttp.ClientSession,
    auth_uri: str,
    headers: dict[str, str],
    config: ConfigAuthXml,
) -> str:
    xml_str = config.create_xml_document_string()
    body = xml_str.encode("utf-8")
    req_headers = dict(headers)
    req_headers.update({
        "Content-Type": "text/xml; charset=utf-8",
        "X-Pad": _x_pad_value(body),
    })
    async with session.post(auth_uri, data=body, headers=req_headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        return await resp.text()


async def obtain_webvpn_cookie(profile: Profile, log: Optional[Callable[[str], None]] = None) -> str:
    log = log or (lambda msg: None)
    server_uri = profile.server_uri.rstrip("/")
    auth_uri = f"{server_uri}/auth"
    connect_uri = f"{server_uri}/CSCOSSLC/tunnel"

    client_env = build_client_environment(
        username=profile.username,
        seed=profile.device_seed,
        av_config=profile.av,
    )
    headers = build_request_headers(client_env, TunnelConfiguration())

    log(f"ClientVersion={client_env.client_version}, OS={client_env.operating_system_information}, Arch={client_env.operating_system_architecture}")
    log(f"AV enabled={client_env.is_av_enabled} updated={client_env.is_av_updated}")

    async with aiohttp.ClientSession() as session:
        log("Requesting discovery endpoint and client ID from NetWall")
        bootstrap_xml = await _post_config_auth(session, server_uri, headers, ConfigAuthXml(client_environment=client_env))
        try:
            parsed = ConfigAuthXml.read_xml(bootstrap_xml)
        except Exception as exc:
            raise ClavisterAuthError(f"Failed to parse bootstrap XML: {exc}") from exc

        if not parsed.discovery_endpoint or not parsed.client_id:
            raise ClavisterAuthError("Server response did not contain discovery-endpoint/client-id")

        log("Starting browser OIDC flow")
        oidc = await start_browser_oidc_flow(session, parsed.discovery_endpoint, parsed.client_id, parsed.nonce)

        params = [
            ConfigAuthXmlParameter(name="id-token", value=oidc.id_token),
            ConfigAuthXmlParameter(name="refresh-token", value=oidc.refresh_token or ""),
        ]
        log("Submitting OIDC tokens to NetWall")
        token_xml = await _post_config_auth(
            session,
            auth_uri,
            headers,
            ConfigAuthXml(parameters=params, authenticator=Authenticator.OIDC),
        )

        try:
            token_reply = ConfigAuthXml.read_xml(token_xml)
        except Exception as exc:
            raise ClavisterAuthError(f"Failed to parse session token XML: {exc}") from exc

        if not token_reply.session_token:
            raise ClavisterAuthError("NetWall did not return a session token")

        log("Issuing CONNECT request to finalize tunnel bootstrap")
        async with session.request("CONNECT", connect_uri, headers=headers, timeout=aiohttp.ClientTimeout(total=15)):
            pass

        return f"webvpn={token_reply.session_token}"
