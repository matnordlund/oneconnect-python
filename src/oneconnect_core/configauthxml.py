from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Union
import xml.etree.ElementTree as ET


class ConfigAuthMessageType(Enum):
    INIT = "init"
    AUTH_REPLY = "auth-reply"
    AUTH_REQUEST = "auth-request"
    COMPLETE = "complete"


class ConfigAuthId(Enum):
    MAIN = "main"
    SUCCESS = "success"
    FAILURE = "failure"


class Authenticator(Enum):
    FORM = "form"
    ONE_TOUCH = "onetouch"
    OIDC = "oidc"


@dataclass(slots=True)
class ConfigAuthXmlParameter:
    name: str
    value: str = ""
    label: Optional[str] = None
    input_type: Optional[str] = None


@dataclass(slots=True)
class ClientEnvironment:
    uid: Optional[str] = None
    client_version: Optional[str] = None
    wolfssl_version: Optional[str] = None
    operating_system_information: Optional[str] = None
    operating_system_architecture: Optional[str] = None
    is_av_enabled: Optional[bool] = None
    is_av_updated: Optional[bool] = None


@dataclass(slots=True)
class ConfigAuthXml:
    message_type: ConfigAuthMessageType = ConfigAuthMessageType.INIT
    auth_id: ConfigAuthId = ConfigAuthId.FAILURE
    authenticator: Authenticator = Authenticator.FORM
    parameters: List[ConfigAuthXmlParameter] = field(default_factory=list)

    message: str = ""
    form_action: Optional[str] = None
    session_token: Optional[str] = None

    client_environment: Optional[ClientEnvironment] = None
    discovery_endpoint: str = ""
    client_id: str = ""
    nonce: Optional[str] = None

    def create_xml_document_string(self) -> str:
        msg_type = self.message_type
        if self.authenticator in (Authenticator.FORM, Authenticator.OIDC):
            msg_type = ConfigAuthMessageType.AUTH_REPLY if self.parameters else ConfigAuthMessageType.INIT
        elif self.authenticator == Authenticator.ONE_TOUCH:
            msg_type = ConfigAuthMessageType.AUTH_REPLY

        root = ET.Element("config-auth", {"client": "vpn", "type": msg_type.value})

        ver = ET.SubElement(root, "version", {"who": "vpn"})
        ver.text = "v2.0"
        device = ET.SubElement(root, "device-id")
        device.text = "linux"

        if msg_type == ConfigAuthMessageType.AUTH_REPLY:
            auth_el = ET.Element("auth")
            if self.authenticator in (Authenticator.FORM, Authenticator.OIDC):
                for p in self.parameters:
                    el = ET.Element(p.name)
                    el.text = p.value or ""
                    auth_el.append(el)
            elif self.authenticator == Authenticator.ONE_TOUCH:
                auth_el.set("authenticator", "onetouch")
            root.append(auth_el)

        if self.client_environment is not None:
            ce = self.client_environment
            ce_el = ET.SubElement(root, "client-environment")

            def add(tag: str, val: Optional[Union[str, bool]]) -> None:
                el = ET.SubElement(ce_el, tag)
                el.text = "" if val is None else str(val)

            add("uid", ce.uid)
            add("client-version", ce.client_version)
            add("wolfssl-version", ce.wolfssl_version)
            add("os-information", ce.operating_system_information)
            add("os-architecture", ce.operating_system_architecture)
            add("av-enabled", ce.is_av_enabled)
            add("av-updated", ce.is_av_updated)

        return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")

    @staticmethod
    def read_xml(xml_string: str) -> "ConfigAuthXml":
        root = ET.fromstring(xml_string)
        if root.tag != "config-auth":
            raise ValueError("Expected root element 'config-auth'")

        type_attr = (root.attrib.get("type") or "").strip()
        if not type_attr:
            raise ValueError("Missing config-auth type")
        msg_type = ConfigAuthMessageType(type_attr)

        auth_nodes = root.findall("auth")
        if len(auth_nodes) != 1:
            raise ValueError(f"Expected exactly one auth element, got {len(auth_nodes)}")
        auth_el = auth_nodes[0]

        auth_id_raw = (auth_el.attrib.get("id") or "").strip()
        if not auth_id_raw:
            raise ValueError("Missing auth id")
        auth_id = ConfigAuthId(auth_id_raw)

        auth_raw = auth_el.attrib.get("authenticator")
        authenticator = Authenticator(auth_raw) if auth_raw else Authenticator.FORM

        message = ((auth_el.findtext("message") or "").strip())
        discovery = ((auth_el.findtext("discovery-endpoint") or "").strip())
        client_id = ((auth_el.findtext("client-id") or "").strip())
        nonce = auth_el.findtext("nonce")

        parameters: List[ConfigAuthXmlParameter] = []
        form_action: Optional[str] = None
        form_nodes = auth_el.findall("form")
        if len(form_nodes) > 1:
            raise ValueError("Expected at most one form element")
        if form_nodes:
            form_el = form_nodes[0]
            form_action = (form_el.attrib.get("action") or "").strip() or None
            for input_el in form_el.findall("input"):
                parameters.append(
                    ConfigAuthXmlParameter(
                        name=input_el.attrib["name"],
                        label=input_el.attrib.get("label"),
                        input_type=input_el.attrib.get("type"),
                    )
                )

        session_token = None
        sess_nodes = root.findall(".//session-token")
        if sess_nodes:
            raw = (sess_nodes[0].text or "").strip()
            session_token = raw if len(raw) > 12 else None

        return ConfigAuthXml(
            message_type=msg_type,
            auth_id=auth_id,
            authenticator=authenticator,
            parameters=parameters,
            message=message,
            form_action=form_action,
            session_token=session_token,
            client_environment=None,
            discovery_endpoint=discovery,
            client_id=client_id,
            nonce=nonce,
        )
