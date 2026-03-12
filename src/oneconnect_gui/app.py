from __future__ import annotations

import asyncio
import threading

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, GLib, Gtk
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "PyGObject/GTK4/libadwaita are required to run the GUI"
    ) from exc

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oneconnect_core.clavister import obtain_webvpn_cookie
from oneconnect_core.openconnect_runner import disconnect_openconnect, run_openconnect
from oneconnect_core.profiles import AVConfig, Profile, ProfileStore

_APP_CSS = """
.status-pill {
    padding: 4px 14px;
    border-radius: 99px;
    font-size: 13px;
    font-weight: 600;
}
.status-disconnected {
    background: alpha(@warning_color, 0.15);
    color: @warning_color;
}
.status-connected {
    background: alpha(@success_color, 0.15);
    color: @success_color;
}
.status-error {
    background: alpha(@error_color, 0.15);
    color: @error_color;
}
.status-busy {
    background: alpha(@accent_color, 0.15);
    color: @accent_color;
}
"""


def _install_css() -> None:
    provider = Gtk.CssProvider()
    try:
        provider.load_from_string(_APP_CSS)
    except (TypeError, AttributeError):
        provider.load_from_data(_APP_CSS.encode())
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


# ---------------------------------------------------------------------------
# Profile editor window
# ---------------------------------------------------------------------------

class ProfileEditWindow(Adw.Window):
    """Profile editor using Adw.EntryRow and PreferencesGroup."""

    def __init__(
        self,
        parent: Gtk.Window,
        profile: Profile | None = None,
        *,
        on_save=None,
    ):
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Edit Profile" if profile else "New Profile",
            default_width=500,
            default_height=720,
        )
        self._profile = profile
        self._on_save = on_save

        # Header
        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        header.pack_start(cancel)
        header.pack_end(save)

        page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=24,
            margin_top=4,
            margin_bottom=24,
        )

        # ── Server ──
        g = Adw.PreferencesGroup(title="Server")
        self.e_name = Adw.EntryRow(title="Profile name")
        self.e_server = Adw.EntryRow(title="NetWall server URI")
        self.e_oc = Adw.EntryRow(title="OpenConnect server override")
        for w in (self.e_name, self.e_server, self.e_oc):
            g.add(w)
        page.append(g)

        # ── Authentication ──
        g = Adw.PreferencesGroup(title="Authentication")
        self.e_user = Adw.EntryRow(title="Username")
        self.e_seed = Adw.EntryRow(title="Device seed")
        self.e_cert = Adw.EntryRow(title="Server certificate pin")
        for w in (self.e_user, self.e_seed, self.e_cert):
            g.add(w)
        page.append(g)

        # ── Advanced ──
        g = Adw.PreferencesGroup(title="Advanced")
        self.e_ua = Adw.EntryRow(title="User-Agent")
        self.e_os = Adw.EntryRow(title="VPN OS")
        self.e_extra = Adw.EntryRow(title="Extra OpenConnect arguments")
        for w in (self.e_ua, self.e_os, self.e_extra):
            g.add(w)
        page.append(g)

        # ── Antivirus / Posture ──
        g = Adw.PreferencesGroup(
            title="Antivirus / Posture",
            description="Controls the AV status reported during authentication",
        )
        self.dd_av = Gtk.DropDown.new_from_strings(["auto", "script", "manual"])
        self.dd_av.set_valign(Gtk.Align.CENTER)
        r = Adw.ActionRow(title="Mode", subtitle="auto \u00b7 script \u00b7 manual")
        r.add_suffix(self.dd_av)
        r.set_activatable_widget(self.dd_av)
        g.add(r)

        self.e_av_script = Adw.EntryRow(title="Script path")
        g.add(self.e_av_script)

        self.sw_en = Gtk.Switch(valign=Gtk.Align.CENTER)
        r = Adw.ActionRow(title="Manual: AV enabled")
        r.add_suffix(self.sw_en)
        r.set_activatable_widget(self.sw_en)
        g.add(r)

        self.sw_up = Gtk.Switch(valign=Gtk.Align.CENTER)
        r = Adw.ActionRow(title="Manual: AV updated")
        r.add_suffix(self.sw_up)
        r.set_activatable_widget(self.sw_up)
        g.add(r)
        page.append(g)

        # Populate fields
        if profile:
            self.e_name.set_text(profile.name)
            self.e_server.set_text(profile.server_uri)
            self.e_oc.set_text(profile.openconnect_server or "")
            self.e_user.set_text(profile.username)
            self.e_seed.set_text(profile.device_seed)
            self.e_cert.set_text(profile.servercert or "")
            self.e_ua.set_text(profile.useragent)
            self.e_os.set_text(profile.vpn_os)
            self.e_extra.set_text(" ".join(profile.extra_openconnect_args))
            modes = ["auto", "script", "manual"]
            self.dd_av.set_selected(
                modes.index(profile.av.mode) if profile.av.mode in modes else 0
            )
            self.e_av_script.set_text(profile.av.script_path or "")
            self.sw_en.set_active(profile.av.manual_enabled)
            self.sw_up.set_active(profile.av.manual_updated)
        else:
            self.e_user.set_text("")
            self.e_seed.set_text("linux-device")
            self.e_ua.set_text("OpenConnect (Clavister OneConnect VPN)")
            self.e_os.set_text("linux")

        self.dd_av.connect("notify::selected", self._av_mode_changed)
        self._av_mode_changed()

        clamp = Adw.Clamp(maximum_size=500, child=page)
        scroll = Gtk.ScrolledWindow(
            child=clamp,
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(header)
        outer.append(scroll)
        self.set_content(outer)

    # -- internals --

    def _av_mode_changed(self, *_args) -> None:
        m = ["auto", "script", "manual"][self.dd_av.get_selected()]
        self.e_av_script.set_sensitive(m == "script")
        self.sw_en.set_sensitive(m == "manual")
        self.sw_up.set_sensitive(m == "manual")

    def _save(self, _btn: Gtk.Button) -> None:
        p = Profile(
            id=self._profile.id if self._profile else Profile().id,
            name=self.e_name.get_text().strip(),
            server_uri=self.e_server.get_text().strip(),
            openconnect_server=self.e_oc.get_text().strip() or None,
            username=self.e_user.get_text().strip() or "user",
            device_seed=self.e_seed.get_text().strip() or "linux-device",
            servercert=self.e_cert.get_text().strip() or None,
            useragent=(
                self.e_ua.get_text().strip()
                or "OpenConnect (Clavister OneConnect VPN)"
            ),
            vpn_os=self.e_os.get_text().strip() or "linux",
            extra_openconnect_args=self.e_extra.get_text().split(),
            av=AVConfig(
                mode=["auto", "script", "manual"][self.dd_av.get_selected()],
                script_path=self.e_av_script.get_text().strip() or None,
                manual_enabled=self.sw_en.get_active(),
                manual_updated=self.sw_up.get_active(),
            ),
        )
        if self._on_save:
            self._on_save(p)
        self.close()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

_STATUS_MAP: dict[str, tuple[str, str]] = {
    "disconnected": ("Disconnected", "status-disconnected"),
    "authenticating": ("Authenticating\u2026", "status-busy"),
    "connecting": ("Connecting\u2026", "status-busy"),
    "connected": ("Connected", "status-connected"),
    "disconnecting": ("Disconnecting\u2026", "status-busy"),
    "error": ("Error", "status-error"),
}


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(
            application=app,
            title="OneConnect",
            default_width=980,
            default_height=640,
        )
        self.store = ProfileStore()
        self.data = self.store.load()
        self.selected_profile: Profile | None = (
            self.data.profiles[0] if self.data.profiles else None
        )
        self.current_proc = None
        self.root_pid = None

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        # ── Sidebar ──────────────────────────────────────────────────────
        sb_header = Adw.HeaderBar(
            show_start_title_buttons=False,
            show_end_title_buttons=False,
        )
        sb_header.set_title_widget(
            Gtk.Label(label="Profiles", css_classes=["heading"])
        )
        add_btn = Gtk.Button(
            icon_name="list-add-symbolic", tooltip_text="Add profile"
        )
        add_btn.connect("clicked", self._on_add)
        sb_header.pack_end(add_btn)

        self.profile_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            css_classes=["navigation-sidebar"],
        )
        self.profile_list.connect("row-selected", self._on_row_selected)

        sb_scroll = Gtk.ScrolledWindow(
            child=self.profile_list,
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar.append(sb_header)
        sidebar.append(sb_scroll)

        # ── Content pane ─────────────────────────────────────────────────
        ct_header = Adw.HeaderBar()

        self.status_label = Gtk.Label(
            label="Disconnected",
            css_classes=["status-pill", "status-disconnected"],
        )
        ct_header.set_title_widget(self.status_label)

        self.connect_btn = Gtk.Button(
            label="Connect", css_classes=["suggested-action"]
        )
        self.connect_btn.connect("clicked", self._on_connect)

        self.disconnect_btn = Gtk.Button(
            label="Disconnect", css_classes=["destructive-action"]
        )
        self.disconnect_btn.connect("clicked", self._on_disconnect)
        self.disconnect_btn.set_sensitive(False)

        ct_header.pack_start(self.connect_btn)
        ct_header.pack_start(self.disconnect_btn)

        edit_hb_btn = Gtk.Button(
            icon_name="document-properties-symbolic",
            tooltip_text="Edit profile",
            css_classes=["flat"],
        )
        edit_hb_btn.connect("clicked", self._on_edit)
        delete_hb_btn = Gtk.Button(
            icon_name="user-trash-symbolic",
            tooltip_text="Delete profile",
            css_classes=["flat"],
        )
        delete_hb_btn.connect("clicked", self._on_delete)
        ct_header.pack_end(delete_hb_btn)
        ct_header.pack_end(edit_hb_btn)

        # Stack: empty state vs detail
        self.content_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
        )
        self.content_stack.add_named(
            Adw.StatusPage(
                icon_name="network-vpn-symbolic",
                title="No Profile Selected",
                description="Add or select a VPN profile from the sidebar",
            ),
            "empty",
        )

        # Detail page
        detail = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            margin_start=16,
            margin_end=16,
            margin_top=8,
            margin_bottom=8,
        )

        self.info_group = Adw.PreferencesGroup(title="Profile")
        self.row_server = Adw.ActionRow(title="NetWall server", subtitle="-")
        self.row_oc = Adw.ActionRow(title="OpenConnect target", subtitle="-")
        self.row_av = Adw.ActionRow(title="AV mode", subtitle="-")
        self.row_advanced = Adw.ActionRow(title="Advanced", subtitle="-")
        for row in (self.row_server, self.row_oc, self.row_av, self.row_advanced):
            self.info_group.add(row)

        info_clamp = Adw.Clamp(maximum_size=700, child=self.info_group)
        detail.append(info_clamp)

        # Log
        log_hdr = Gtk.Box(spacing=8, margin_top=16, margin_bottom=4)
        log_hdr.append(
            Gtk.Label(label="Log", css_classes=["heading"], hexpand=True, xalign=0)
        )
        clear_btn = Gtk.Button(
            icon_name="edit-clear-all-symbolic",
            css_classes=["flat"],
            tooltip_text="Clear log",
        )
        clear_btn.connect("clicked", lambda _: self.log_buffer.set_text(""))
        log_hdr.append(clear_btn)
        detail.append(log_hdr)

        self.log_view = Gtk.TextView(
            editable=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=8,
            bottom_margin=8,
            left_margin=12,
            right_margin=12,
        )
        self.log_buffer = self.log_view.get_buffer()
        log_scroll = Gtk.ScrolledWindow(child=self.log_view, vexpand=True)
        log_frame = Gtk.Frame(child=log_scroll, vexpand=True)
        detail.append(log_frame)

        self.content_stack.add_named(detail, "detail")

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(ct_header)
        content_box.append(self.content_stack)

        # ── Split view ───────────────────────────────────────────────────
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_start_child(sidebar)
        paned.set_end_child(content_box)
        paned.set_position(280)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)

        self.toast_overlay.set_child(paned)
        self._refresh_list()
        self._refresh_detail()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _toast(self, msg: str) -> None:
        GLib.idle_add(lambda: self.toast_overlay.add_toast(Adw.Toast(title=msg)))

    def append_log(self, text: str) -> None:
        def _do():
            end = self.log_buffer.get_end_iter()
            self.log_buffer.insert(end, text + "\n")
            mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
            self.log_view.scroll_to_mark(mark, 0, False, 0, 0)
        GLib.idle_add(_do)

    def set_status(self, state: str) -> None:
        def _do():
            label, css_cls = _STATUS_MAP.get(state, ("Unknown", "status-error"))
            self.status_label.set_label(label)
            for cls in ("status-disconnected", "status-connected", "status-error", "status-busy"):
                self.status_label.remove_css_class(cls)
            self.status_label.add_css_class(css_cls)
            busy = state in ("authenticating", "connecting", "disconnecting")
            self.connect_btn.set_sensitive(not busy and state != "connected")
            self.disconnect_btn.set_sensitive(state == "connected" or busy)
        GLib.idle_add(_do)

    # ── List / detail refresh ─────────────────────────────────────────────

    def _refresh_list(self) -> None:
        while row := self.profile_list.get_first_child():
            self.profile_list.remove(row)
        self.data = self.store.load()
        selected_idx = 0
        for idx, p in enumerate(self.data.profiles):
            row = Gtk.ListBoxRow()
            box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=2,
                margin_top=6,
                margin_bottom=6,
                margin_start=6,
                margin_end=6,
            )
            box.append(Gtk.Label(label=p.name, xalign=0, css_classes=["heading"]))
            box.append(
                Gtk.Label(
                    label=p.server_uri,
                    xalign=0,
                    css_classes=["dim-label", "caption"],
                )
            )
            row.set_child(box)
            row.profile = p  # type: ignore[attr-defined]
            self.profile_list.append(row)
            if self.selected_profile and p.id == self.selected_profile.id:
                selected_idx = idx
        if self.data.profiles:
            self.profile_list.select_row(
                self.profile_list.get_row_at_index(selected_idx)
            )
        else:
            self.selected_profile = None
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        p = self.selected_profile
        if not p:
            self.content_stack.set_visible_child_name("empty")
            return
        self.content_stack.set_visible_child_name("detail")
        self.info_group.set_title(p.name)
        self.row_server.set_subtitle(p.server_uri or "\u2014")
        self.row_oc.set_subtitle(p.openconnect_server or p.server_uri or "\u2014")
        av = p.av.mode
        if p.av.script_path:
            av += f"  ({p.av.script_path})"
        elif p.av.mode == "manual":
            av += f"  enabled={p.av.manual_enabled}  updated={p.av.manual_updated}"
        self.row_av.set_subtitle(av)
        adv = f"os={p.vpn_os}  ua={p.useragent}"
        if p.extra_openconnect_args:
            adv += f"  extra={' '.join(p.extra_openconnect_args)}"
        self.row_advanced.set_subtitle(adv)

    # ── Callbacks ─────────────────────────────────────────────────────────

    def _on_row_selected(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        self.selected_profile = getattr(row, "profile", None) if row else None
        self._refresh_detail()

    def _on_add(self, _btn: Gtk.Button) -> None:
        def save(p: Profile) -> None:
            try:
                self.store.upsert_profile(p)
                self.selected_profile = p
                self._refresh_list()
                self._toast("Profile added")
            except Exception as exc:
                self._toast(f"Error: {exc}")

        ProfileEditWindow(self, on_save=save).present()

    def _on_edit(self, _btn: Gtk.Button) -> None:
        if not self.selected_profile:
            return

        def save(p: Profile) -> None:
            try:
                self.store.upsert_profile(p)
                self.selected_profile = p
                self._refresh_list()
                self._toast("Profile saved")
            except Exception as exc:
                self._toast(f"Error: {exc}")

        ProfileEditWindow(self, profile=self.selected_profile, on_save=save).present()

    def _on_delete(self, _btn: Gtk.Button) -> None:
        if not self.selected_profile:
            return
        name = self.selected_profile.name
        self.store.delete_profile(self.selected_profile.id)
        self.selected_profile = None
        self._refresh_list()
        self._toast(f"Deleted {name}")

    def _on_connect(self, _btn: Gtk.Button) -> None:
        profile = self.selected_profile
        if not profile:
            self._toast("Select a profile first")
            return

        def worker() -> None:
            async def run() -> None:
                try:
                    self.set_status("authenticating")
                    cookie = await obtain_webvpn_cookie(profile, log=self.append_log)
                    self.append_log("Received session cookie, launching OpenConnect")
                    self.set_status("connecting")
                    rc = await run_openconnect(
                        profile,
                        cookie,
                        log=self.append_log,
                        use_pkexec=True,
                        proc_holder=self,
                    )
                    self.set_status("disconnected")
                    self.append_log(f"OpenConnect exited ({rc})")
                except Exception as exc:
                    self.append_log(f"ERROR: {exc}")
                    self.set_status("error")

            asyncio.run(run())

        threading.Thread(target=worker, daemon=True).start()

    def _on_disconnect(self, _btn: Gtk.Button) -> None:
        if not self.selected_profile and not self.current_proc:
            self._toast("No active connection")
            return

        def worker() -> None:
            async def run() -> None:
                try:
                    self.set_status("disconnecting")
                    rc = await disconnect_openconnect(
                        self.root_pid,
                        profile=self.selected_profile,
                        log=self.append_log,
                        use_pkexec=True,
                    )
                    self.append_log(f"Disconnect exited ({rc})")
                    self.set_status("disconnected")
                except Exception as exc:
                    self.append_log(f"ERROR: {exc}")
                    self.set_status("error")

            asyncio.run(run())

        threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class OneConnectApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="com.clavister.OneConnect")
        self._css_loaded = False

    def do_activate(self) -> None:
        if not self._css_loaded:
            _install_css()
            self._css_loaded = True
        win = self.props.active_window or MainWindow(self)
        win.present()


def main() -> None:
    OneConnectApp().run(None)


if __name__ == "__main__":
    main()
