"""GTK3 systray GUI for OneConnect (Ubuntu/Yaru, Ayatana AppIndicator)."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

# On Linux, help venv find system typelibs (Ubuntu/Debian often need this)
if sys.platform == "linux":
    _gipath = [p for p in os.environ.get("GI_TYPELIB_PATH", "").split(os.pathsep) if p]
    for _d in (
        "/usr/lib/x86_64-linux-gnu/girepository-1.0",
        "/usr/lib/aarch64-linux-gnu/girepository-1.0",
        "/usr/lib/girepository-1.0",
    ):
        if os.path.isdir(_d) and _d not in _gipath:
            _gipath.insert(0, _d)
    if _gipath:
        os.environ["GI_TYPELIB_PATH"] = os.pathsep.join(_gipath)

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango
    # Prefer Ayatana (Ubuntu/Debian), fall back to older AppIndicator3 (some distros)
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
    except (ValueError, ImportError):
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AppIndicator
except (ValueError, ImportError, ModuleNotFoundError) as exc:
    _msg = str(exc)
    if "gi" in _msg.lower() and ("module" in _msg.lower() or "no module" in _msg.lower()):
        raise SystemExit(
            "The 'gi' module (PyGObject) is not installed in this environment.\n"
            "On Ubuntu/Debian, install system packages and run the GUI with system Python:\n"
            "  sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1\n"
            "  python3 -m oneconnect_gui.app\n"
            "Or use a venv created with --system-site-packages so it can see the system's python3-gi."
        ) from exc
    raise SystemExit(
        "PyGObject with Gtk 3.0 and an app indicator are required.\n"
        "Install: gir1.2-gtk-3.0 and one of gir1.2-ayatanaappindicator3-0.1 or gir1.2-appindicator3-0.1.\n"
        f"Error: {exc}"
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


# ---------------------------------------------------------------------------
# Log viewer window: terminal-style tail -f
# ---------------------------------------------------------------------------

class LogViewerWindow(Gtk.Window):
    """Terminal-style window showing tail -f of the profile log file."""

    def __init__(self, profile: Profile) -> None:
        path = get_openconnect_log_file_path(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
        name = profile.name or profile.id[:12]
        super().__init__(title=f"Log: {name}")
        self.set_default_size(700, 400)
        self._path = path
        self._proc: subprocess.Popen | None = None
        self._watch_id: int | None = None

        # Terminal-like styling
        self.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.15, 0.15, 0.15, 1.0))
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        self._text = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            wrap_mode=Gtk.WrapMode.CHAR,
            left_margin=8,
            right_margin=8,
            top_margin=8,
            bottom_margin=8,
        )
        self._text.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.9, 0.9, 0.9, 1.0))
        fd = Pango.FontDescription.from_string("Monospace 10")
        self._text.override_font(fd)
        self._buffer = self._text.get_buffer()
        scroll.add(self._text)
        self.add(scroll)

        self.connect("destroy", self._on_destroy)
        self.show_all()

        try:
            self._proc = subprocess.Popen(
                ["tail", "-f", "-n", "500", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except FileNotFoundError:
            self._buffer.set_text("tail command not found.")
            return
        if self._proc.stdout is None:
            return
        self._watch_id = GLib.io_add_watch(
            self._proc.stdout.fileno(),
            GLib.IO_IN,
            self._on_stdout,
        )

    def _on_stdout(self, _source: int, _condition: int) -> bool:
        if self._proc is None or self._proc.stdout is None:
            return False
        try:
            data = os.read(self._proc.stdout.fileno(), 4096)
        except OSError:
            return False
        if not data:
            return False
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            return True
        end = self._buffer.get_end_iter()
        self._buffer.insert(end, text)
        # Scroll to end
        self._text.scroll_to_iter(self._buffer.get_end_iter(), 0, False, 0, 0)
        return True

    def _on_destroy(self, _window: Gtk.Window) -> None:
        if self._watch_id is not None:
            GLib.source_remove(self._watch_id)
            self._watch_id = None
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None


def _open_log(profile: Profile) -> None:
    """Open a terminal-style window that tails the profile log file."""
    LogViewerWindow(profile)


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
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._menu = Gtk.Menu()
        self._connect_submenu = None
        self._building = False
        self._last_connected_id: str | None = None
        self.refresh_menu()
        # Poll connection state so tray stays in sync if user connects/disconnects via CLI
        GLib.timeout_add_seconds(2, self._poll_connection_state)

    def _poll_connection_state(self) -> bool:
        """Called periodically; refresh tray only when connection state actually changed. Return True to keep polling."""
        connected = _find_connected_profile(self.store)
        current_id = connected.id if connected else None
        if current_id != self._last_connected_id:
            self.refresh_menu()
        return True

    def refresh_menu(self) -> None:
        if self._building:
            return
        self._building = True
        # Clear existing items
        for c in self._menu.get_children():
            self._menu.remove(c)
        connected = _find_connected_profile(self.store)
        profiles = self.store.load().profiles
        self._last_connected_id = connected.id if connected else None

        if connected:
            green_path = _green_tinted_icon_path()
            if green_path:
                self.indicator.set_icon(green_path)
            else:
                self.indicator.set_icon_full(ICON_DISCONNECTED, "icon")
            name = connected.name or connected.id[:12]
            info = get_tunnel_status(connected)
            ip = (info or {}).get("connection_ip") if info else None
            self.indicator.set_title(f"{name}: Connected, using IP {ip}" if ip else f"{name}: Connected")
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
            self.indicator.set_title("Unconnected")
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
        status_text = (
            f"{name}: Connected, using IP {ip}" if ip else f"{name}: Connected"
        ) if connected else "Unconnected"
        status_item = Gtk.MenuItem(label=status_text)
        status_item.set_sensitive(False)
        self._menu.append(status_item)
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
        self.set_default_size(440, 160)
        self.set_resizable(True)
        self._profile = profile
        self._on_save = on_save
        box = self.get_content_area()
        box.set_border_width(12)
        box.set_spacing(12)
        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        grid.set_vexpand(True)
        grid.set_hexpand(True)
        row = 0
        grid.attach(Gtk.Label(label="Profile name:", halign=Gtk.Align.END, valign=Gtk.Align.CENTER), 0, row, 1, 1)
        self.e_name = Gtk.Entry(hexpand=True)
        self.e_name.set_text((profile.name or "") if profile else "")
        grid.attach(self.e_name, 1, row, 1, 1)
        row += 1
        grid.attach(Gtk.Label(label="NetWall server URI:", halign=Gtk.Align.END, valign=Gtk.Align.CENTER), 0, row, 1, 1)
        self.e_server = Gtk.Entry(hexpand=True)
        self.e_server.set_text((profile.server_uri or "") if profile else "")
        grid.attach(self.e_server, 1, row, 1, 1)
        box.pack_start(grid, True, True, 0)
        box.show_all()
        self.connect("response", self._on_response)

    def _on_response(self, _dlg: Gtk.Dialog, resp: int) -> None:
        if resp != Gtk.ResponseType.OK:
            return
        name = self.e_name.get_text().strip()
        server = self.e_server.get_text().strip()
        if not name or not server:
            return
        if self._profile:
            p = Profile(
                id=self._profile.id,
                name=name,
                server_uri=server,
                username=self._profile.username,
                device_seed=self._profile.device_seed,
                openconnect_server=self._profile.openconnect_server,
                servercert=self._profile.servercert,
                useragent=self._profile.useragent,
                vpn_os=self._profile.vpn_os,
                extra_openconnect_args=list(self._profile.extra_openconnect_args),
                av=AVConfig(**__import__("dataclasses").asdict(self._profile.av)),
            )
        else:
            p = Profile(name=name, server_uri=server, av=AVConfig())
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
        self.connect_btn = Gtk.Button(label="Connect", image=Gtk.Image.new_from_icon_name("network-connect-symbolic", Gtk.IconSize.BUTTON))
        self.connect_btn.connect("clicked", self._on_connect)
        add_btn = Gtk.Button(label="Add", image=Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON))
        add_btn.connect("clicked", self._on_add)
        edit_btn = Gtk.Button(label="Edit", image=Gtk.Image.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.BUTTON))
        edit_btn.connect("clicked", self._on_edit)
        delete_btn = Gtk.Button(label="Delete", image=Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON))
        delete_btn.connect("clicked", self._on_delete)
        toolbar.pack_start(self.connect_btn, False, False, 0)
        toolbar.pack_start(add_btn, False, False, 0)
        toolbar.pack_start(edit_btn, False, False, 0)
        toolbar.pack_start(delete_btn, False, False, 0)
        box.pack_start(toolbar, False, False, 0)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self._on_activated)
        scroll.add(self.listbox)
        box.pack_start(scroll, True, True, 0)
        hint = Gtk.Label(
            label="Select a profile and click Connect. If the tray icon is missing, install "
            "gnome-shell-extension-appindicator and enable AppIndicators (see README).",
            wrap=True,
            xalign=0,
            margin_top=8,
        )
        hint.get_style_context().add_class("dim-label")
        box.pack_start(hint, False, False, 0)
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

    def _on_connect(self, _btn: Gtk.Button | None) -> None:
        p = self._selected_profile()
        if not p:
            return
        self.connect_btn.set_sensitive(False)

        def run() -> None:
            async def do_connect() -> None:
                backend = get_backend(use_networkmanager=False, use_pkexec=True)
                secrets = await obtain_webvpn_secrets(p, log=lambda m: None)
                await backend.connect(p, secrets, log=lambda m: None)
            asyncio.run(do_connect())
            GLib.idle_add(lambda: self.connect_btn.set_sensitive(True))
            if self.on_refresh_tray:
                GLib.idle_add(self.on_refresh_tray)
        threading.Thread(target=run, daemon=True).start()

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
    if not Gtk.init_check():
        raise SystemExit("Could not initialize GTK. Is DISPLAY set? Run from a graphical session.")
    store = ProfileStore()
    manager_ref: list = []
    tray_ref: list = []

    def show_manager() -> None:
        if manager_ref:
            win = manager_ref[0]
            win.present()
            return
        if tray_ref:
            win = ProfileManagerWindow(store, on_refresh_tray=lambda: tray_ref[0].refresh_menu())
        else:
            win = ProfileManagerWindow(store, on_refresh_tray=lambda: None)
        manager_ref.append(win)
        win.connect("destroy", lambda w: manager_ref.clear() if w in manager_ref else None)
        win.show_all()

    def setup_tray() -> bool:
        """Create indicator after main loop is running so the panel is ready (idle_add callback)."""
        tray = TrayController(store, on_show_manager=show_manager)
        tray_ref.append(tray)
        if "--manage-profiles" in sys.argv:
            GLib.idle_add(show_manager)
        return False  # one-shot

    GLib.idle_add(setup_tray)
    Gtk.main()


if __name__ == "__main__":
    main()
