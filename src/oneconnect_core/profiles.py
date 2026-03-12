from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import uuid
from typing import Optional
from urllib.parse import urlparse


CONFIG_DIR = Path.home() / ".config" / "oneconnect"
PROFILES_FILE = CONFIG_DIR / "profiles.json"


def normalize_server_uri(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return value
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid server URI: {value}")
    return f"{parsed.scheme}://{parsed.netloc}"



@dataclass(slots=True)
class AVConfig:
    mode: str = "auto"  # auto | script | manual
    script_path: Optional[str] = None
    manual_enabled: bool = False
    manual_updated: bool = False


@dataclass(slots=True)
class Profile:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    server_uri: str = ""
    openconnect_server: Optional[str] = None
    username: str = "user"
    device_seed: str = "linux-device"
    servercert: Optional[str] = None
    useragent: str = "OpenConnect (Clavister OneConnect VPN)"
    vpn_os: str = "linux"
    extra_openconnect_args: list[str] = field(default_factory=list)
    av: AVConfig = field(default_factory=AVConfig)

    def __post_init__(self) -> None:
        self.server_uri = normalize_server_uri(self.server_uri)
        if self.openconnect_server:
            self.openconnect_server = normalize_server_uri(self.openconnect_server)


@dataclass(slots=True)
class ProfileStoreData:
    last_used_profile_id: Optional[str] = None
    profiles: list[Profile] = field(default_factory=list)


class ProfileStore:
    def __init__(self, path: Path = PROFILES_FILE) -> None:
        self.path = path

    def load(self) -> ProfileStoreData:
        if not self.path.exists():
            return ProfileStoreData()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        profiles = []
        for item in raw.get("profiles", []):
            av_raw = item.get("av", {})
            item = dict(item)
            item["server_uri"] = normalize_server_uri(item.get("server_uri", ""))
            if item.get("openconnect_server"):
                item["openconnect_server"] = normalize_server_uri(item["openconnect_server"])
            if not item.get("vpn_os") or item.get("vpn_os") == "win":
                item["vpn_os"] = "linux"
            item["av"] = AVConfig(**av_raw)
            profiles.append(Profile(**item))
        return ProfileStoreData(
            last_used_profile_id=raw.get("last_used_profile_id"),
            profiles=profiles,
        )

    def save(self, data: ProfileStoreData) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_used_profile_id": data.last_used_profile_id,
            "profiles": [
                {
                    **asdict(p),
                    "av": asdict(p.av),
                }
                for p in data.profiles
            ],
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def upsert_profile(self, profile: Profile) -> None:
        data = self.load()
        replaced = False
        for idx, existing in enumerate(data.profiles):
            if existing.id == profile.id:
                data.profiles[idx] = profile
                replaced = True
                break
        if not replaced:
            data.profiles.append(profile)
        data.last_used_profile_id = profile.id
        self.save(data)

    def delete_profile(self, profile_id: str) -> None:
        data = self.load()
        data.profiles = [p for p in data.profiles if p.id != profile_id]
        if data.last_used_profile_id == profile_id:
            data.last_used_profile_id = data.profiles[0].id if data.profiles else None
        self.save(data)

    def get_by_name(self, name: str) -> Optional[Profile]:
        for p in self.load().profiles:
            if p.name == name:
                return p
        return None
