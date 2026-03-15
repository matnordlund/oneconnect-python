"""Connection backends: direct openconnect subprocess vs NetworkManager."""
from __future__ import annotations

from typing import Callable, Optional, Protocol

from .profiles import Profile
from .clavister import SessionSecrets


class ConnectionBackend(Protocol):
    """Backend that runs the VPN tunnel (direct openconnect or NetworkManager)."""

    async def connect(
        self,
        profile: Profile,
        secrets: SessionSecrets,
        log: Optional[Callable[[str], None]] = None,
        proc_holder: object | None = None,
    ) -> int:
        """Start the tunnel. Returns exit code (0 = still running until disconnect)."""
        ...

    async def disconnect(
        self,
        profile: Profile,
        root_pid: Optional[int] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> int:
        """Stop the tunnel. Returns exit code."""
        ...


class DirectBackend:
    """Run openconnect as a subprocess (current behavior)."""

    def __init__(self, use_pkexec: bool = True) -> None:
        self.use_pkexec = use_pkexec

    async def connect(
        self,
        profile: Profile,
        secrets: SessionSecrets,
        log: Optional[Callable[[str], None]] = None,
        proc_holder: object | None = None,
    ) -> int:
        from .openconnect_runner import run_openconnect
        return await run_openconnect(
            profile,
            secrets.cookie,
            log=log,
            use_pkexec=self.use_pkexec,
            proc_holder=proc_holder,
        )

    async def disconnect(
        self,
        profile: Profile,
        root_pid: Optional[int] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> int:
        from .openconnect_runner import disconnect_openconnect
        # Daemon runs as the invoking user (--setuid), so no pkexec needed to kill it
        return await disconnect_openconnect(
            root_pid,
            profile=profile,
            log=log,
            use_pkexec=False,
        )


class NetworkManagerBackend:
    """Use NetworkManager to activate/deactivate the openconnect VPN."""

    async def connect(
        self,
        profile: Profile,
        secrets: SessionSecrets,
        log: Optional[Callable[[str], None]] = None,
        proc_holder: object | None = None,
    ) -> int:
        from .networkmanager import activate_nm_connection
        return await activate_nm_connection(profile, secrets, log=log)

    async def disconnect(
        self,
        profile: Profile,
        root_pid: Optional[int] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> int:
        from .networkmanager import deactivate_nm_connection
        return await deactivate_nm_connection(profile, log=log)


def get_backend(use_networkmanager: bool, use_pkexec: bool = True) -> ConnectionBackend:
    """Return the appropriate backend."""
    if use_networkmanager:
        return NetworkManagerBackend()
    return DirectBackend(use_pkexec=use_pkexec)
