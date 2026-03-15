"""GTK3 systray GUI for OneConnect (Ubuntu/Yaru, Ayatana AppIndicator)."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from pathlib import Path

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
    # Prefer Ayatana (Ubuntu/Debian), fall back to older AppIndicator3 (some distros)
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
    except (ValueError, ImportError):
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AppIndicator
except (ValueError, ImportError) as exc:
    raise SystemExit(
        "PyGObject with Gtk 3.0 and an app indicator are required.\n"
        "Install one of: gir1.2-ayatanaappindicator3-0.1 (Ubuntu/Debian) or "
        "gir1.2-appindicator3-0.1 (older distros). Also: gir1.2-gtk-3.0."
    ) from exc

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oneconnect_core.clavister import obtain_webvpn_secrets, SessionSecrets
from oneconnect_core.profiles import AVConfig, CONFIG_DIR, Profile, ProfileStore
from oneconnect_core.runner import get_backend
from oneconnect_core.openconnect_runner import get_openconnect_log_file_path, get_tunnel_status

INDICATOR_ID = "oneconnect"
ICON_DISCONNECTED = "network-vpn-symbolic"
TRAY_ICON_SIZE = 22
# Green tint for "connected" state (RGBA-ish: used to tint the symbolic icon)
GREEN_TINT = (0x2e, 0xcc, 0x71)  # Yaru success-style green

_connected_icon_path: str | None = None


def _green_tinted_icon_path() -> str | None:
    """Create a green-tinted tray icon and return its path, or None on failure."""
    global _connected_icon_path
    theme = Gtk.IconTheme.get_default()
    try:
        pixbuf = theme.load_icon(ICON_DISCONNECTED, TRAY_ICON_SIZE, 0)
    except Exception:
        return None
    if pixbuf is None:
        return None
    w = pixbuf.get_width()
    h = pixbuf.get_height()
    has_alpha = pixbuf.get_has_alpha()
    n_channels = pixbuf.get_n_channels()
    rowstride = pixbuf.get_rowstride()
    pixels = pixbuf.get_pixels()
    if not pixels:
        return None
    # New buffer: same layout (keep rowstride), RGB = green scaled by alpha
    new_data = bytearray(pixels)
    r, g, b = GREEN_TINT
    for y in range(h):
        for x in range(w):
            i = y * rowstride + x * n_channels
            if has_alpha and n_channels >= 4:
                a = pixels[i + 3]
            else:
                a = 255
            new_data[i] = int(r * a / 255)
            new_data[i + 1] = int(g * a / 255)
            new_data[i + 2] = int(b * a / 255)
            if n_channels >= 4:
                new_data[i + 3] = a
    try:
        new_pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(bytes(new_data)),
            GdkPixbuf.Colorspace.RGB,
            has_alpha,
            8,
            w,
            h,
            rowstride,
        )
    except Exception:
        return None
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / "tray-connected.png"
    try:
        new_pixbuf.savev(str(path), "png", [], [])
    except Exception:
        return None
    _connected_icon_path = str(path)
    return _connected_icon_path


def _find_connected_profile(store: ProfileStore):
    """Return the profile that has an active tunnel, or None."""
    for p in store.load().profiles:
        if get_tunnel_status(p) is not None:
            return p
    return None


def _open_log(profile: Profile) -> None:
    path = get_openconnect_log_file_path(profile)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    try:
        subprocess.run(["xdg-open", str(path)], check=False, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


# ---------------------------------------------------------------------------
# Indicator controller: tray icon + menu, refresh on state change
# ---------------------------------------------------------------------------

class TrayController:
    def __init__(self, store: ProfileStore, on_show_manager=None):
        self.store = store
        self.on_show_manager = on_show_manager
        self.indicator = AppIndicator.Indicator.new(
            INDICATOR_ID,
            ICON_DISCONNECTED,
            AppIndicator.IndicatorCategory.SYSTEM_SERVICES,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._menu = Gtk.Menu()
        self._connect_submenu = None
        self._building = False
        self.refresh_menu()

    def refresh_menu(self) -> None:
        if self._building:
            return
        self._building = True
        # Clear existing items
        for c in self._menu.get_children():
            self._menu.remove(c)
        connected = _find_connected_profile(self.store)
        profiles = self.store.load().profiles

        if connected:
            green_path = _green_tinted_icon_path()
            if green_path:
                self.indicator.set_icon(green_path)
            else:
                self.indicator.set_icon_full(ICON_DISCONNECTED, "icon")
            name = connected.name or connected.id[:12]
            lab = Gtk.MenuItem(label=f"Connected: {name}")
            lab.set_sensitive(False)
            self._menu.append(lab)
            sep = Gtk.SeparatorMenuItem()
            self._menu.append(sep)
            disc = Gtk.MenuItem(label="Disconnect")
            disc.connect("activate", self._on_disconnect, connected)
            self._menu.append(disc)
            view_log = Gtk.MenuItem(label="View log")
            view_log.connect("activate", self._on_view_log, connected)
            self._menu.append(view_log)
        else:
            self.indicator.set_icon_full(ICON_DISCONNECTED, "icon")
            connect_item = Gtk.MenuItem(label="Connect to")
            self._connect_submenu = Gtk.Menu()
            connect_item.set_submenu(self._connect_submenu)
            self._menu.append(connect_item)
            for p in profiles:
                name = p.name or p.id[:12]
                mi = Gtk.MenuItem(label=name)
                mi.connect("activate", self._on_connect, p)
                self._connect_submenu.append(mi)
            if not profiles:
                mi = Gtk.MenuItem(label="(no profiles)")
                mi.set_sensitive(False)
                self._connect_submenu.append(mi)

        sep2 = Gtk.SeparatorMenuItem()
        self._menu.append(sep2)
        manage = Gtk.MenuItem(label="Manage profiles")
        manage.connect("activate", self._on_manage)
        self._menu.append(manage)
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_quit)
        self._menu.append(quit_item)
        self._menu.show_all()
        self.indicator.set_menu(self._menu)
        self._building = False

    def _on_connect(self, _mi: Gtk.MenuItem, profile: Profile) -> None:
        def run() -> None:
            async def do_connect() -> None:
                backend = get_backend(use_networkmanager=False, use_pkexec=True)
                secrets = await obtain_webvpn_secrets(profile, log=lambda m: None)
                await backend.connect(profile, secrets, log=lambda m: None)
            asyncio.run(do_connect())
            GLib.idle_add(self.refresh_menu)
            # Refresh again after a short delay so the pid file is visible
            GLib.timeout_add(800, self.refresh_menu)
        threading.Thread(target=run, daemon=True).start()

    def _on_disconnect(self, _mi: Gtk.MenuItem, profile: Profile) -> None:
        def run() -> None:
            async def do_disconnect() -> None:
                backend = get_backend(use_networkmanager=False, use_pkexec=True)
                await backend.disconnect(profile, root_pid=None, log=lambda m: None)
            asyncio.run(do_disconnect())
            GLib.idle_add(self.refresh_menu)
        threading.Thread(target=run, daemon=True).start()

    def _on_view_log(self, _mi: Gtk.MenuItem, profile: Profile) -> None:
        _open_log(profile)

    def _on_manage(self, _mi: Gtk.MenuItem) -> None:
        if self.on_show_manager:
            self.on_show_manager()

    def _on_quit(self, _mi: Gtk.MenuItem) -> None:
        Gtk.main_quit()


# ---------------------------------------------------------------------------
# Profile editor dialog (Add / Edit)
# ---------------------------------------------------------------------------

class ProfileEditDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, profile: Profile | None, on_save=None):
        title = "Edit profile" if profile else "New profile"
        super().__init__(title=title, transient_for=parent, modal=True)
        self.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        self._profile = profile
        self._on_save = on_save
        box = self.get_content_area()
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, margin=18)
        row = 0
        grid.attach(Gtk.Label(label="Profile name:", halign=Gtk.Align.END), 0, row, 1, 1)
        self.e_name = Gtk.Entry(hexpand=True)
        self.e_name.set_text((profile.name or "") if profile else "")
        grid.attach(self.e_name, 1, row, 1, 1)
        row += 1
        grid.attach(Gtk.Label(label="NetWall server URI:", halign=Gtk.Align.END), 0, row, 1, 1)
        self.e_server = Gtk.Entry(hexpand=True)
        self.e_server.set_text((profile.server_uri or "") if profile else "")
        grid.attach(self.e_server, 1, row, 1, 1)
        row += 1
        grid.attach(Gtk.Label(label="Username:", halign=Gtk.Align.END), 0, row, 1, 1)
        self.e_username = Gtk.Entry(hexpand=True)
        self.e_username.set_text((profile.username or "user") if profile else "user")
        grid.attach(self.e_username, 1, row, 1, 1)
        row += 1
        grid.attach(Gtk.Label(label="Device seed:", halign=Gtk.Align.END), 0, row, 1, 1)
        self.e_device = Gtk.Entry(hexpand=True)
        self.e_device.set_text((profile.device_seed or "linux-device") if profile else "linux-device")
        grid.attach(self.e_device, 1, row, 1, 1)
        box.add(grid)
        self.connect("response", self._on_response)

    def _on_response(self, _dlg: Gtk.Dialog, resp: int) -> None:
        if resp != Gtk.ResponseType.OK:
            return
        name = self.e_name.get_text().strip()
        server = self.e_server.get_text().strip()
        if not name or not server:
            return
        username = self.e_username.get_text().strip() or "user"
        device = self.e_device.get_text().strip() or "linux-device"
        if self._profile:
            p = Profile(
                id=self._profile.id,
                name=name,
                server_uri=server,
                username=username,
                device_seed=device,
                openconnect_server=self._profile.openconnect_server,
                servercert=self._profile.servercert,
                useragent=self._profile.useragent,
                vpn_os=self._profile.vpn_os,
                extra_openconnect_args=list(self._profile.extra_openconnect_args),
                av=AVConfig(**__import__("dataclasses").asdict(self._profile.av)),
            )
        else:
            p = Profile(
                name=name,
                server_uri=server,
                username=username,
                device_seed=device,
                av=AVConfig(),
            )
        if self._on_save:
            self._on_save(p)


# ---------------------------------------------------------------------------
# Profile manager window
# ---------------------------------------------------------------------------

class ProfileManagerWindow(Gtk.Window):
    def __init__(self, store: ProfileStore, on_refresh_tray=None):
        super().__init__(title="OneConnect – Profiles")
        self.set_default_size(480, 360)
        self.store = store
        self.on_refresh_tray = on_refresh_tray
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=12)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_btn = Gtk.Button(label="Add", image=Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON))
        add_btn.connect("clicked", self._on_add)
        edit_btn = Gtk.Button(label="Edit", image=Gtk.Image.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.BUTTON))
        edit_btn.connect("clicked", self._on_edit)
        delete_btn = Gtk.Button(label="Delete", image=Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON))
        delete_btn.connect("clicked", self._on_delete)
        toolbar.pack_start(add_btn, False, False, 0)
        toolbar.pack_start(edit_btn, False, False, 0)
        toolbar.pack_start(delete_btn, False, False, 0)
        box.pack_start(toolbar, False, False, 0)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self._on_activated)
        scroll.add(self.listbox)
        box.pack_start(scroll, True, True, 0)
        self.add(box)
        self._fill()
        self.connect("destroy", self._on_destroy)

    def _fill(self) -> None:
        for c in self.listbox.get_children():
            self.listbox.remove(c)
        for p in self.store.load().profiles:
            row = Gtk.ListBoxRow()
            lab = Gtk.Label(label=p.name or p.id[:12], xalign=0)
            row.add(lab)
            row.profile = p
            self.listbox.add(row)
        self.listbox.show_all()

    def _on_destroy(self, _w: Gtk.Window) -> None:
        if self.on_refresh_tray:
            self.on_refresh_tray()

    def _selected_profile(self) -> Profile | None:
        row = self.listbox.get_selected_row()
        return getattr(row, "profile", None) if row else None

    def _on_activated(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self._on_edit(None)

    def _on_add(self, _btn: Gtk.Button) -> None:
        dlg = ProfileEditDialog(self, None, on_save=self._saved)
        dlg.run()
        dlg.destroy()

    def _on_edit(self, _btn: Gtk.Button | None) -> None:
        p = self._selected_profile()
        if not p:
            return
        dlg = ProfileEditDialog(self, p, on_save=self._saved)
        dlg.run()
        dlg.destroy()

    def _on_delete(self, _btn: Gtk.Button) -> None:
        p = self._selected_profile()
        if not p:
            return
        d = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete profile “{p.name or p.id[:12]}”?",
        )
        d.format_secondary_text("This cannot be undone.")
        if d.run() == Gtk.ResponseType.YES:
            self.store.delete_profile(p.id)
            self._fill()
            if self.on_refresh_tray:
                self.on_refresh_tray()
        d.destroy()

    def _saved(self, profile: Profile) -> None:
        self.store.upsert_profile(profile)
        self._fill()
        if self.on_refresh_tray:
            self.on_refresh_tray()


# ---------------------------------------------------------------------------
# Application entry
# ---------------------------------------------------------------------------

def main() -> None:
    store = ProfileStore()
    manager_ref = []

    def show_manager() -> None:
        if manager_ref:
            win = manager_ref[0]
            win.present()
            return
        win = ProfileManagerWindow(store, on_refresh_tray=lambda: tray.refresh_menu())
        manager_ref.append(win)
        win.connect("destroy", lambda w: manager_ref.clear() if w in manager_ref else None)
        win.show_all()

    tray = TrayController(store, on_show_manager=show_manager)

    # Optional: if no profiles, open manager so user can add one
    if not store.load().profiles:
        GLib.idle_add(show_manager)

    Gtk.main()


if __name__ == "__main__":
    main()
