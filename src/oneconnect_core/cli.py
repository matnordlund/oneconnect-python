"""CLI entry point for OneConnect (list, add-profile, connect, disconnect, status)."""
from __future__ import annotations

import argparse
import asyncio
import json

from oneconnect_core.clavister import obtain_webvpn_cookie, obtain_webvpn_secrets, SessionSecrets
from oneconnect_core.config import get_use_networkmanager
from oneconnect_core.openconnect_runner import get_tunnel_status
from oneconnect_core.profiles import AVConfig, Profile, ProfileStore
from oneconnect_core.runner import get_backend


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    add = sub.add_parser("add-profile")
    add.add_argument("--name", required=True)
    add.add_argument("--server-uri", required=True)
    add.add_argument("--username", default="user")
    add.add_argument("--device-seed", default="linux-device")

    connect = sub.add_parser("connect")
    connect.add_argument("name")
    connect.add_argument("--no-pkexec", action="store_true", help="Run openconnect directly without pkexec")
    connect.add_argument("--nm", "--network-manager", dest="use_nm", action="store_true", help="Use NetworkManager to run the VPN")
    connect.add_argument("--no-nm", dest="use_nm", action="store_false", help="Run openconnect directly (default)")
    connect.set_defaults(use_nm=None)

    disconnect = sub.add_parser("disconnect")
    disconnect.add_argument("name")
    disconnect.add_argument("--no-pkexec", action="store_true", help="Run disconnect directly without pkexec")
    disconnect.add_argument("--nm", "--network-manager", dest="use_nm", action="store_true", help="Use NetworkManager to disconnect")
    disconnect.add_argument("--no-nm", dest="use_nm", action="store_false", help="Disconnect direct openconnect (default)")
    disconnect.set_defaults(use_nm=None)

    status = sub.add_parser("status")
    status.add_argument("name", help="Profile name to check for an active tunnel")

    args = parser.parse_args()
    store = ProfileStore()

    if args.cmd == "list":
        data = store.load()
        print(json.dumps([
            {"id": p.id, "name": p.name, "server_uri": p.server_uri, "username": p.username}
            for p in data.profiles
        ], indent=2))
        return

    if args.cmd == "add-profile":
        profile = Profile(
            name=args.name,
            server_uri=args.server_uri,
            username=args.username,
            device_seed=args.device_seed,
            av=AVConfig(),
        )
        store.upsert_profile(profile)
        print(f"Saved profile {args.name} -> {profile.server_uri}")
        return

    if args.cmd == "disconnect":
        profile = store.get_by_name(args.name)
        if not profile:
            raise SystemExit(f"Profile not found: {args.name}")
        use_nm = args.use_nm if args.use_nm is not None else get_use_networkmanager()
        backend = get_backend(use_networkmanager=use_nm, use_pkexec=not args.no_pkexec)

        async def run_disconnect() -> None:
            rc = await backend.disconnect(profile, root_pid=None, log=print)
            raise SystemExit(rc)
        asyncio.run(run_disconnect())
        return

    if args.cmd == "status":
        profile = store.get_by_name(args.name)
        if not profile:
            raise SystemExit(f"Profile not found: {args.name}")
        info = get_tunnel_status(profile)
        if info is None:
            print("Not connected")
        else:
            ip = info.get("connection_ip")
            if ip:
                print(f"Connected using IP {ip}")
            else:
                print("Connected (tunnel active; see log for details)")
        return

    if args.cmd == "connect":
        profile = store.get_by_name(args.name)
        if not profile:
            raise SystemExit(f"Profile not found: {args.name}")
        use_nm = args.use_nm if args.use_nm is not None else get_use_networkmanager()

        async def run() -> None:
            secrets: SessionSecrets = await obtain_webvpn_secrets(profile, log=print)
            backend = get_backend(use_networkmanager=use_nm, use_pkexec=not args.no_pkexec)
            # If NetworkManager is requested but we lack a fingerprint/connect URL,
            # fall back to direct openconnect for reliability.
            if use_nm and not secrets.fingerprint:
                print("NetworkManager backend missing gateway fingerprint; falling back to direct openconnect.")
                backend = get_backend(use_networkmanager=False, use_pkexec=not args.no_pkexec)
            rc = await backend.connect(profile, secrets, log=print)
            raise SystemExit(rc)
        asyncio.run(run())


if __name__ == "__main__":
    main()
