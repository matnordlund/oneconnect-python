from __future__ import annotations

import asyncio
import threading

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib
except Exception as exc:  # pragma: no cover
    raise SystemExit("PyGObject/GTK4/libadwaita are required to run the GUI") from exc

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oneconnect_core.clavister import obtain_webvpn_cookie
from oneconnect_core.openconnect_runner import disconnect_openconnect, run_openconnect
from oneconnect_core.profiles import AVConfig, Profile, ProfileStore


class ProfileDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, profile: Profile | None = None):
        super().__init__(title="Edit Profile" if profile else "Add Profile", transient_for=parent, modal=True)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Save", Gtk.ResponseType.OK)
        self.profile = profile

        content = self.get_content_area()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        content.append(outer)

        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        outer.append(grid)

        self.name = Gtk.Entry(text=profile.name if profile else "")
        self.server = Gtk.Entry(text=profile.server_uri if profile else "")
        self.openconnect_server = Gtk.Entry(text=profile.openconnect_server or "" if profile else "")
        self.username = Gtk.Entry(text=profile.username if profile else "user")
        self.device_seed = Gtk.Entry(text=profile.device_seed if profile else "linux-device")
        self.servercert = Gtk.Entry(text=profile.servercert or "" if profile else "")
        self.useragent = Gtk.Entry(text=profile.useragent if profile else "OpenConnect (Clavister OneConnect VPN)")
        self.vpn_os = Gtk.Entry(text=profile.vpn_os if profile else "linux")
        self.extra_args = Gtk.Entry(text=" ".join(profile.extra_openconnect_args) if profile else "")

        fields = [
            ("Name", self.name),
            ("NetWall server", self.server),
            ("OpenConnect server override", self.openconnect_server),
            ("Username", self.username),
            ("Device seed", self.device_seed),
            ("Server certificate", self.servercert),
            ("User-Agent", self.useragent),
            ("VPN OS", self.vpn_os),
            ("Extra OpenConnect args", self.extra_args),
        ]
        for row, (label, widget) in enumerate(fields):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)

        av_frame = Gtk.Frame(label="Antivirus / posture")
        outer.append(av_frame)
        av_grid = Gtk.Grid(column_spacing=12, row_spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        av_frame.set_child(av_grid)

        av = profile.av if profile else AVConfig()
        self.av_mode = Gtk.DropDown.new_from_strings(["auto", "script", "manual"])
        self.av_mode.set_selected(["auto", "script", "manual"].index(av.mode if av.mode in {"auto", "script", "manual"} else "auto"))
        self.av_script = Gtk.Entry(text=av.script_path or "")
        self.manual_enabled = Gtk.Switch(active=av.manual_enabled)
        self.manual_updated = Gtk.Switch(active=av.manual_updated)
        self.av_help = Gtk.Label(
            label="script mode accepts TRUE/FALSE or enabled=TRUE updated=TRUE",
            xalign=0,
            wrap=True,
        )
        av_grid.attach(Gtk.Label(label="Mode", xalign=0), 0, 0, 1, 1)
        av_grid.attach(self.av_mode, 1, 0, 1, 1)
        av_grid.attach(Gtk.Label(label="Script path", xalign=0), 0, 1, 1, 1)
        av_grid.attach(self.av_script, 1, 1, 1, 1)
        av_grid.attach(Gtk.Label(label="Manual enabled", xalign=0), 0, 2, 1, 1)
        av_grid.attach(self.manual_enabled, 1, 2, 1, 1)
        av_grid.attach(Gtk.Label(label="Manual updated", xalign=0), 0, 3, 1, 1)
        av_grid.attach(self.manual_updated, 1, 3, 1, 1)
        av_grid.attach(self.av_help, 0, 4, 2, 1)
        self.av_mode.connect("notify::selected", self.on_av_mode_changed)
        self.on_av_mode_changed()

    def on_av_mode_changed(self, *_args) -> None:
        mode = self.get_av_mode()
        self.av_script.set_sensitive(mode == "script")
        self.manual_enabled.set_sensitive(mode == "manual")
        self.manual_updated.set_sensitive(mode == "manual")

    def get_av_mode(self) -> str:
        return ["auto", "script", "manual"][self.av_mode.get_selected()]

    def build_profile(self) -> Profile:
        profile_id = self.profile.id if self.profile else None
        return Profile(
            id=profile_id or Profile().id,
            name=self.name.get_text().strip(),
            server_uri=self.server.get_text().strip(),
            openconnect_server=self.openconnect_server.get_text().strip() or None,
            username=self.username.get_text().strip() or "user",
            device_seed=self.device_seed.get_text().strip() or "linux-device",
            servercert=self.servercert.get_text().strip() or None,
            useragent=self.useragent.get_text().strip() or "OpenConnect (Clavister OneConnect VPN)",
            vpn_os=self.vpn_os.get_text().strip() or "linux",
            extra_openconnect_args=self.extra_args.get_text().split(),
            av=AVConfig(
                mode=self.get_av_mode(),
                script_path=self.av_script.get_text().strip() or None,
                manual_enabled=self.manual_enabled.get_active(),
                manual_updated=self.manual_updated.get_active(),
            ),
        )


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="OneConnect Linux")
        self.set_default_size(980, 640)
        self.store = ProfileStore()
        self.data = self.store.load()
        self.selected_profile: Profile | None = self.data.profiles[0] if self.data.profiles else None

        self.header = Adw.HeaderBar()
        self.connect_button = Gtk.Button(label="Connect")
        self.disconnect_button = Gtk.Button(label="Disconnect")
        self.add_button = Gtk.Button(label="Add")
        self.edit_button = Gtk.Button(label="Edit")
        self.delete_button = Gtk.Button(label="Delete")
        self.connect_button.connect("clicked", self.on_connect_clicked)
        self.disconnect_button.connect("clicked", self.on_disconnect_clicked)
        self.add_button.connect("clicked", self.on_add_clicked)
        self.edit_button.connect("clicked", self.on_edit_clicked)
        self.delete_button.connect("clicked", self.on_delete_clicked)
        self.header.pack_start(self.add_button)
        self.header.pack_start(self.edit_button)
        self.header.pack_start(self.delete_button)
        self.header.pack_end(self.disconnect_button)
        self.header.pack_end(self.connect_button)

        self.profile_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.profile_list.connect("row-selected", self.on_row_selected)
        self.log_view = Gtk.TextView(editable=False, monospace=True)
        self.log_buffer = self.log_view.get_buffer()
        self.status = Gtk.Label(label="Disconnected", xalign=0)
        self.detail_name = Gtk.Label(xalign=0)
        self.detail_server = Gtk.Label(xalign=0)
        self.detail_av = Gtk.Label(xalign=0)
        self.detail_args = Gtk.Label(xalign=0, wrap=True)
        self.current_proc = None
        self.root_pid = None

        details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        details_box.append(self.status)
        details_box.append(self.detail_name)
        details_box.append(self.detail_server)
        details_box.append(self.detail_av)
        details_box.append(self.detail_args)
        details_box.append(Gtk.Separator())
        details_box.append(Gtk.ScrolledWindow(child=self.log_view, hexpand=True, vexpand=True))

        split = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        split.set_start_child(Gtk.ScrolledWindow(child=self.profile_list, min_content_width=300))
        split.set_end_child(details_box)
        split.set_position(300)

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        layout.append(self.header)
        layout.append(split)
        self.set_content(layout)
        self.refresh_profiles()
        self.refresh_details()

    def append_log(self, text: str) -> None:
        def _do() -> None:
            end = self.log_buffer.get_end_iter()
            self.log_buffer.insert(end, text + "\n")
        GLib.idle_add(_do)

    def set_status(self, text: str) -> None:
        GLib.idle_add(self.status.set_label, text)

    def refresh_profiles(self) -> None:
        while row := self.profile_list.get_first_child():
            self.profile_list.remove(row)
        self.data = self.store.load()
        selected_idx = 0
        for idx, profile in enumerate(self.data.profiles):
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
            box.append(Gtk.Label(label=profile.name, xalign=0))
            box.append(Gtk.Label(label=profile.server_uri, xalign=0))
            row.set_child(box)
            row.profile = profile
            self.profile_list.append(row)
            if self.selected_profile and profile.id == self.selected_profile.id:
                selected_idx = idx
        if self.data.profiles:
            self.profile_list.select_row(self.profile_list.get_row_at_index(selected_idx))
        else:
            self.selected_profile = None
        self.refresh_details()

    def refresh_details(self) -> None:
        p = self.selected_profile
        if not p:
            self.detail_name.set_label("No profile selected")
            self.detail_server.set_label("")
            self.detail_av.set_label("")
            self.detail_args.set_label("")
            return
        self.detail_name.set_label(f"Profile: {p.name}")
        server = p.openconnect_server or p.server_uri
        self.detail_server.set_label(f"NetWall: {p.server_uri}    OpenConnect target: {server}")
        av_desc = f"AV mode: {p.av.mode}"
        if p.av.script_path:
            av_desc += f" ({p.av.script_path})"
        elif p.av.mode == "manual":
            av_desc += f" enabled={p.av.manual_enabled} updated={p.av.manual_updated}"
        self.detail_av.set_label(av_desc)
        self.detail_args.set_label(
            f"Advanced: os={p.vpn_os}  useragent={p.useragent}  extra_args={' '.join(p.extra_openconnect_args) or '-'}"
        )

    def on_row_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        self.selected_profile = getattr(row, "profile", None) if row else None
        self.refresh_details()

    def open_profile_dialog(self, profile: Profile | None = None) -> None:
        dialog = ProfileDialog(self, profile=profile)

        def _resp(_dlg: Gtk.Dialog, response: int) -> None:
            if response == Gtk.ResponseType.OK:
                try:
                    updated = dialog.build_profile()
                    self.store.upsert_profile(updated)
                    self.selected_profile = updated
                    self.refresh_profiles()
                except Exception as exc:
                    self.append_log(f"ERROR: {exc}")
            dialog.destroy()
        dialog.connect("response", _resp)
        dialog.present()

    def on_add_clicked(self, _button: Gtk.Button) -> None:
        self.open_profile_dialog(None)

    def on_edit_clicked(self, _button: Gtk.Button) -> None:
        if not self.selected_profile:
            self.append_log("No profile selected")
            return
        self.open_profile_dialog(self.selected_profile)

    def on_delete_clicked(self, _button: Gtk.Button) -> None:
        if not self.selected_profile:
            self.append_log("No profile selected")
            return
        profile_id = self.selected_profile.id
        self.store.delete_profile(profile_id)
        self.selected_profile = None
        self.refresh_profiles()
        self.append_log("Profile deleted")

    def on_connect_clicked(self, _button: Gtk.Button) -> None:
        profile = self.selected_profile
        if not profile:
            self.append_log("No profile selected")
            return

        def worker() -> None:
            async def run() -> None:
                try:
                    self.set_status("Authenticating...")
                    cookie = await obtain_webvpn_cookie(profile, log=self.append_log)
                    self.append_log("Received session cookie, launching OpenConnect")
                    self.set_status("Connecting...")
                    rc = await run_openconnect(profile, cookie, log=self.append_log, use_pkexec=True, proc_holder=self)
                    self.set_status(f"Disconnected (exit {rc})")
                except Exception as exc:
                    self.append_log(f"ERROR: {exc}")
                    self.set_status("Error")
            asyncio.run(run())

        threading.Thread(target=worker, daemon=True).start()

    def on_disconnect_clicked(self, _button: Gtk.Button) -> None:
        if not self.selected_profile and not self.current_proc:
            self.append_log("No active OpenConnect process")
            return

        def worker() -> None:
            async def run() -> None:
                try:
                    self.set_status("Disconnecting...")
                    rc = await disconnect_openconnect(self.root_pid, profile=self.selected_profile, log=self.append_log, use_pkexec=True)
                    self.append_log(f"Disconnect command exited with {rc}")
                    self.set_status("Disconnected")
                except Exception as exc:
                    self.append_log(f"ERROR: failed to disconnect: {exc}")
                    self.set_status("Error")
            asyncio.run(run())

        threading.Thread(target=worker, daemon=True).start()


class OneConnectApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="com.example.OneConnectLinux")
        Adw.init()

    def do_activate(self) -> None:
        win = self.props.active_window
        if not win:
            win = MainWindow(self)
        win.present()


def main() -> None:
    app = OneConnectApp()
    app.run(None)


if __name__ == "__main__":
    main()
