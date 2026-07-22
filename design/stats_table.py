"""
Prototipo: Tabla comparativa de planificadores para Tendencia.
2 opciones: Grid, Compacto.
Ejecutar: python design/stats_table.py
"""

import math
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

MOCK_SCHEDS = ["scx_lavd", "scx_bpfland", "scx_rusty", "scx_central", "scx_ghost"]
MOCK_STATS = {
    "scx_lavd":    {"avg": 1234, "min": 1100, "max": 1500, "last": 1300, "std": 120, "count": 8},
    "scx_bpfland": {"avg": 1456, "min": 1200, "max": 1700, "last": 1600, "std": 150, "count": 6},
    "scx_rusty":   {"avg": 1345, "min": 1150, "max": 1600, "last": 1250, "std": 130, "count": 10},
    "scx_central": {"avg": 1678, "min": 1400, "max": 1900, "last": 1800, "std": 160, "count": 5},
    "scx_ghost":   {"avg": 1123, "min": 1000, "max": 1300, "last": 1100, "std": 100, "count": 7},
}
SCHED_COLORS = {
    "scx_lavd":    (0.96, 0.76, 0.07),
    "scx_bpfland": (0.18, 0.76, 0.49),
    "scx_rusty":   (0.90, 0.33, 0.30),
    "scx_central": (0.40, 0.50, 0.90),
    "scx_ghost":   (0.70, 0.40, 0.90),
}
COLUMNS = ["avg", "min", "max", "last", "std"]
COL_LABELS = ["Promedio", "Mínimo", "Máximo", "Último", "σ"]


class PrototypeWindow(Adw.ApplicationWindow):

    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Prototipo: Tabla Comparativa")
        self.set_default_size(620, 520)
        self._build_ui()

    def _build_ui(self):
        header = Adw.HeaderBar()

        self.stack = Adw.ViewStack()
        self.stack.add_titled(self._build_grid(), "grid", "Grid")
        self.stack.add_titled(self._build_compact(), "compact", "Compacto")

        tab_box = Gtk.Box(spacing=0, css_classes=["linked"])
        for name, key in [("Grid", "grid"), ("Compacto", "compact")]:
            btn = Gtk.ToggleButton(label=name, css_classes=["flat"])
            btn.connect("toggled", lambda b, k=key: self.stack.set_visible_child_name(k) if b.get_active() else None)
            tab_box.append(btn)
        header.set_title_widget(tab_box)

        footer = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=12)
        approve = Gtk.Button(label="Aprobar", css_classes=["suggested-action", "pill"])
        approve.connect("clicked", lambda *_: self._toast("✅ Opción seleccionada"))
        other = Gtk.Button(label="Otro", css_classes=["pill"])
        other.connect("clicked", lambda *_: self._switch_tab())
        footer.append(approve)
        footer.append(other)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.stack)
        content.append(footer)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(content)

        view = Adw.ToolbarView(content=self.toast_overlay)
        view.add_top_bar(header)
        self.set_content(view)

        first_btn = tab_box.get_first_child()
        if first_btn:
            first_btn.set_active(True)

    def _toast(self, msg):
        self.toast_overlay.add_toast(Adw.Toast.new(msg))

    def _switch_tab(self):
        current = self.stack.get_visible_child_name()
        new = "compact" if current == "grid" else "grid"
        self.stack.set_visible_child_name(new)

    # ── Opción Grid ──
    def _build_grid(self):
        page = Adw.PreferencesPage()
        grupo = Adw.PreferencesGroup(title="Comparativa de Planificadores")

        grid = Gtk.Grid(
            column_spacing=16, row_spacing=6,
            margin_start=12, margin_end=12,
            margin_top=8, margin_bottom=8,
        )

        # Encabezados
        grid.attach(Gtk.Label(), 0, 0, 1, 1)
        for ci, name in enumerate(COL_LABELS):
            lbl = Gtk.Label(label=name, css_classes=["dim-label", "caption-heading"], halign=Gtk.Align.CENTER)
            grid.attach(lbl, ci + 1, 0, 1, 1)

        # Filas
        for ri, sched in enumerate(MOCK_SCHEDS, start=1):
            st = MOCK_STATS[sched]
            r, g, b = SCHED_COLORS.get(sched, (0.5, 0.5, 0.5))

            # Dot + nombre
            name_box = Gtk.Box(spacing=6)
            dot = Gtk.DrawingArea()
            dot.set_content_width(8)
            dot.set_content_height(8)
            dot.set_valign(Gtk.Align.CENTER)
            dot.set_draw_func(lambda a, cr, w, h, rr=r, gg=g, bb=b: (
                cr.set_source_rgb(rr, gg, bb),
                cr.arc(w / 2, h / 2, 3.5, 0, 2 * math.pi),
                cr.fill(),
            ))
            name_box.append(dot)
            name_box.append(Gtk.Label(label=sched, css_classes=["caption-heading"]))
            grid.attach(name_box, 0, ri, 1, 1)

            # Valores
            for ci, col in enumerate(COLUMNS):
                val = st[col]
                is_best = col == "avg" and val == min(s["avg"] for s in MOCK_STATS.values())
                lbl = Gtk.Label(
                    label=f"{val:,.0f}",
                    css_classes=["caption-heading"] if not is_best else ["accent", "caption-heading"],
                    halign=Gtk.Align.END,
                )
                grid.attach(lbl, ci + 1, ri, 1, 1)

        grupo.add(grid)
        page.add(grupo)
        return page

    # ── Opción Compacto ──
    def _build_compact(self):
        page = Adw.PreferencesPage()

        for sched in MOCK_SCHEDS:
            st = MOCK_STATS[sched]
            r, g, b = SCHED_COLORS.get(sched, (0.5, 0.5, 0.5))

            grupo = Adw.PreferencesGroup(title=sched)

            header_box = Gtk.Box(spacing=6, margin_start=6, margin_bottom=4)
            dot = Gtk.DrawingArea()
            dot.set_content_width(8)
            dot.set_content_height(8)
            dot.set_valign(Gtk.Align.CENTER)
            dot.set_draw_func(lambda a, cr, w, h, rr=r, gg=g, bb=b: (
                cr.set_source_rgb(rr, gg, bb),
                cr.arc(w / 2, h / 2, 3.5, 0, 2 * math.pi),
                cr.fill(),
            ))
            header_box.append(dot)
            grupo.set_header_suffix(header_box)

            inner = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=12,
                margin_start=12, margin_end=12,
                margin_top=6, margin_bottom=6,
            )

            row_vals = Gtk.Box(spacing=0, hexpand=True)
            for ci, col in enumerate(COLUMNS):
                col_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True, halign=Gtk.Align.CENTER)
                col_box.append(Gtk.Label(label=COL_LABELS[ci], css_classes=["dim-label", "caption"]))
                col_box.append(Gtk.Label(label=f"{st[col]:,.0f}", css_classes=["caption-heading"]))
                row_vals.append(col_box)

            inner.append(row_vals)

            grupo.add(inner)
            page.add(grupo)

        return page


class PrototypeApp(Adw.Application):

    def __init__(self):
        super().__init__()
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        self.win = PrototypeWindow(app)
        self.win.present()


if __name__ == "__main__":
    PrototypeApp().run()
