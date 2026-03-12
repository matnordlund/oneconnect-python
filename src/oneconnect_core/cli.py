"""CLI entry point for OneConnect (list, add-profile, connect, disconnect)."""
from __future__ import annotations

import argparse
import asyncio
import json

from oneconnect_core.clavister import obtain_webvpn_cookie
from oneconnect_core.openconnect_runner import disconnect_openconnect, run_openconnect
from oneconnect_core.profiles import AVConfig, Profile, ProfileStore


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

    disconnect = sub.add_parser("disconnect")
    disconnect.add_argument("name")
    disconnect.add_argument("--no-pkexec", action="store_true", help="Run disconnect directly without pkexec")

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

        async def run_disconnect() -> None:
            rc = await disconnect_openconnect(None, profile=profile, log=print, use_pkexec=not args.no_pkexec)
            raise SystemExit(rc)
        asyncio.run(run_disconnect())
        return

    if args.cmd == "connect":
        profile = store.get_by_name(args.name)
        if not profile:
            raise SystemExit(f"Profile not found: {args.name}")

        async def run() -> None:
            cookie = await obtain_webvpn_cookie(profile, log=print)
            rc = await run_openconnect(profile, cookie, log=print, use_pkexec=not args.no_pkexec)
            raise SystemExit(rc)
        asyncio.run(run())


if __name__ == "__main__":
    main()
