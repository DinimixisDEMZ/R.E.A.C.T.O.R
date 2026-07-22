"""
Prototipo: Flyout de historial tipo GNOME Calendar para Automatización.
MenuButton + Popover + ListBox con scroll.
Ejecutar: python design/history_flyout.py
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk


MOCK_RUNS = [
    ("scx_bpfland", "auto", "16/07/26 09:45", "6.10.13", True),
    ("scx_rusty", "powersave", "15/07/26 14:32", "6.10.13", False),
    ("scx_lavd", "gaming", "15/07/26 10:10", "6.10.13", True),
    ("scx_central", "auto", "14/07/26 18:00", "6.10.12", False),
    ("scx_bpfland", "lowlatency", "14/07/26 08:22", "6.10.12", True),
    ("scx_ghost", "server", "13/07/26 20:15", "6.10.12", False),
    ("scx_rusty", "auto", "13/07/26 11:00", "6.10.12", True),
    ("scx_dummy", "auto", "12/07/26 16:40", "6.10.11", False),
    ("scx_bpfland", "gaming", "12/07/26 09:00", "6.10.11", True),
    ("scx_lavd", "auto", "11/07/26 14:20", "6.10.11", False),
    ("scx_central", "powersave", "11/07/26 08:05", "6.10.11", True),
    ("scx_ghost", "auto", "10/07/26 22:30", "6.10.10", False),
]


class CardRow(Gtk.ListBoxRow):
    """Fila de historial con diseño compacto."""

    def __init__(self, scheduler, mode, date, kernel, is_winner):
        super().__init__()
        box = Gtk.Box(spacing=10, margin_start=10, margin_end=10, margin_top=6, margin_bottom=6)

        dot = Gtk.DrawingArea()
        dot.set_content_width(10)
        dot.set_content_height(10)
        dot.set_valign(Gtk.Align.CENTER)
        color = Gdk.RGBA()
        if is_winner:
            color.parse("#2ec27e")
        else:
            color.parse("#f5c211")
        dot.set_draw_func(lambda a, cr, w, h, c=color: _draw_dot(cr, w, h, c))
        box.append(dot)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_row = Gtk.Box(spacing=6)
        sched_lbl = Gtk.Label(label=scheduler, css_classes=["heading"])
        title_row.append(sched_lbl)
        mode_lbl = Gtk.Label(label=mode, css_classes=["caption", "dim-label"])
        title_row.append(mode_lbl)
        col.append(title_row)

        sub_row = Gtk.Box(spacing=8)
        date_lbl = Gtk.Label(label=date, css_classes=["caption", "dim-label"])
        sub_row.append(date_lbl)
        kernel_lbl = Gtk.Label(label=f"Kernel {kernel}", css_classes=["caption", "dim-label"])
        sub_row.append(kernel_lbl)
        col.append(sub_row)

        box.append(col)
        self.set_child(box)


def _draw_dot(cr, w, h, color):
    cx, cy, r = w / 2, h / 2, 4
    cr.set_source_rgba(color.red, color.green, color.blue, 0.9)
    cr.arc(cx, cy, r, 0, 2 * 3.14159)
    cr.fill()


class FlyoutWindow(Adw.ApplicationWindow):

    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Prototipo: Flyout de Historial")
        self.set_default_size(500, 350)

        self.selected_index = 2
        self._build_ui()

    def _build_ui(self):
        header = Adw.HeaderBar()

        self.menu_btn = Gtk.MenuButton(
            icon_name="document-open-recent-symbolic",
            tooltip_text="Historial de runs",
            css_classes=["flat"],
            always_show_arrow=True,
        )
        header.pack_start(self.menu_btn)

        lbl_title = Gtk.Label(label="Automatización", css_classes=["title-4"])
        header.set_title_widget(lbl_title)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          margin_top=24, margin_start=24, margin_end=24)
        info = Gtk.Label(
            label="↖ Presioná el botón de historial en el header\n"
                  "para ver el prototipo del flyout.",
            justify=Gtk.Justification.CENTER,
        )
        info.add_css_class("title-4")
        content.append(info)

        self.info_label = Gtk.Label(
            label="Seleccionado: scx_bpfland [auto] - 16/07/26",
            css_classes=["subtitle"],
        )
        content.append(self.info_label)

        self._build_popover()

        view = Adw.ToolbarView(content=content)
        view.add_top_bar(header)
        self.set_content(view)

    def _build_popover(self):
        self.popover = Gtk.Popover()
        self.popover.set_size_request(320, 350)

        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
            propagate_natural_height=True,
        )

        self.listbox = Gtk.ListBox(
            css_classes=["boxed-list"],
            selection_mode=Gtk.SelectionMode.SINGLE,
        )
        self._populate_list()
        self.listbox.connect("row-selected", self._on_row_selected)
        self.listbox.select_row(self.listbox.get_row_at_index(self.selected_index))

        scroll.set_child(self.listbox)
        self.popover.set_child(scroll)
        self.menu_btn.set_popover(self.popover)

    def _populate_list(self):
        while self.listbox.get_last_child():
            self.listbox.remove(self.listbox.get_last_child())

        for i, (sched, mode, date, kernel, winner) in enumerate(MOCK_RUNS):
            row = CardRow(sched, mode, date, kernel, winner)
            row.run_index = i
            self.listbox.append(row)

        self._update_header_label()

    def _on_row_selected(self, listbox, row):
        if row is None:
            return
        idx = getattr(row, "run_index", -1)
        if idx < 0 or idx >= len(MOCK_RUNS):
            return
        sched, mode, date, kernel, winner = MOCK_RUNS[idx]
        self.selected_index = idx
        self.info_label.set_label(
            f"Seleccionado: {sched} [{mode}] - {date} (Kernel {kernel})"
        )
        self._update_header_label()

    def _update_header_label(self):
        idx = self.selected_index
        total = len(MOCK_RUNS)
        self.menu_btn.set_label(f"Run {idx + 1} de {total}")


class FlyoutApp(Adw.Application):

    def __init__(self):
        super().__init__()
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        self.win = FlyoutWindow(app)
        self.win.present()


if __name__ == "__main__":
    FlyoutApp().run()
