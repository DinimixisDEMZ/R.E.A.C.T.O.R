"""
Prototipo: Alternativas de visualización de información del planificador.
Ejecutar: python design/sched_info.py
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

MOCK_SCHED = "bpfland"
MOCK_DESC = "Planificador basado en BPF con balance justo entre rendimiento y latencia. Ideal para uso general."
MOCK_URL = "https://github.com/sched-ext/scx"


class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Prototipo: Info del planificador")
        self.set_default_size(500, 500)
        header = Adw.HeaderBar()
        stack = Adw.ViewStack()

        stack.add_titled(self._opt_a(), "a", "A: ExpanderRow")
        stack.add_titled(self._opt_b(), "b", "B: ActionRow")
        stack.add_titled(self._opt_c(), "c", "C: Card inline")
        stack.add_titled(self._opt_d(), "d", "D: Revealer")

        btns = Gtk.Box(spacing=0, css_classes=["linked"])
        for key, label in [("a", "Expander"), ("b", "ActionRow"), ("c", "Card"), ("d", "Revealer")]:
            b = Gtk.ToggleButton(label=label, css_classes=["flat"])
            b.connect("toggled", lambda btn, k=key: stack.set_visible_child_name(k) if btn.get_active() else None)
            btns.append(b)
        btns.get_first_child().set_active(True)
        header.set_title_widget(btns)

        view = Adw.ToolbarView(content=stack)
        view.add_top_bar(header)
        self.set_content(view)

    def _opt_a(self):
        """ExpanderRow - actual"""
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Información del Planificador")
        exp = Adw.ExpanderRow(title=MOCK_SCHED, subtitle="Descripción del planificador")
        lbl = Gtk.Label(label=MOCK_DESC, xalign=0, wrap=True, css_classes=["dim-label"], margin_top=4, margin_bottom=4)
        exp.add_row(lbl)
        link = Gtk.LinkButton(label="Abrir en navegador", uri=MOCK_URL, valign=Gtk.Align.CENTER)
        r = Adw.ActionRow(title="Documentación oficial")
        r.add_suffix(link)
        exp.add_row(r)
        g.add(exp)
        page.add(g)
        return page

    def _opt_b(self):
        """ActionRow - descripción visible siempre"""
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Información del Planificador")
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["boxed-list"], margin_top=6)
        top = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=10)
        dot = Gtk.DrawingArea()
        dot.set_content_width(10); dot.set_content_height(10); dot.set_valign(Gtk.Align.CENTER)
        dot.set_draw_func(lambda a, cr, w, h: (
            cr.set_source_rgb(0.18, 0.76, 0.49),
            cr.arc(w / 2, h / 2, 4, 0, 2 * 3.14159),
            cr.fill(),
        ))
        top.append(dot)
        top.append(Gtk.Label(label=MOCK_SCHED, css_classes=["title-4"], xalign=0))
        top.append(Gtk.Label(label="Descripción", css_classes=["dim-label", "caption"], valign=Gtk.Align.CENTER))
        row.append(top)
        desc = Gtk.Label(label=MOCK_DESC, xalign=0, wrap=True, margin_start=12, margin_end=12, margin_bottom=8)
        row.append(desc)
        link_row = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_bottom=8)
        link_row.append(Gtk.Image(icon_name="web-browser-symbolic", pixel_size=12, css_classes=["dim-label"]))
        link_row.append(Gtk.LinkButton(label="Documentación oficial", uri=MOCK_URL))
        row.append(link_row)
        g.add(row)
        page.add(g)
        return page

    def _opt_c(self):
        """Card inline"""
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Información del Planificador")
        card = Gtk.Frame(css_classes=["card"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=12, margin_end=12, margin_top=10, margin_bottom=10)
        title_row = Gtk.Box(spacing=6)
        title_row.append(Gtk.Image(icon_name="application-x-executable-symbolic", pixel_size=14))
        title_row.append(Gtk.Label(label=MOCK_SCHED, css_classes=["heading"], xalign=0))
        box.append(title_row)
        desc = Gtk.Label(label=MOCK_DESC, xalign=0, wrap=True, css_classes=["dim-label"])
        box.append(desc)
        box.append(Gtk.LinkButton(label="Abrir documentación »", uri=MOCK_URL, halign=Gtk.Align.START))
        card.set_child(box)
        g.add(card)
        page.add(g)
        return page

    def _opt_d(self):
        """Revealer con slide-down"""
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Información del Planificador")
        revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, reveal_child=True)
        card = Gtk.Frame(css_classes=["card"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=12, margin_end=12, margin_top=10, margin_bottom=10)
        title_row = Gtk.Box(spacing=6)
        dot = Gtk.DrawingArea()
        dot.set_content_width(8); dot.set_content_height(8); dot.set_valign(Gtk.Align.CENTER)
        dot.set_draw_func(lambda a, cr, w, h: (
            cr.set_source_rgb(0.18, 0.76, 0.49),
            cr.arc(w / 2, h / 2, 3.5, 0, 2 * 3.14159),
            cr.fill(),
        ))
        title_row.append(dot)
        title_row.append(Gtk.Label(label=MOCK_SCHED, css_classes=["heading"], xalign=0))
        box.append(title_row)
        desc = Gtk.Label(label=MOCK_DESC, xalign=0, wrap=True, css_classes=["dim-label"])
        box.append(desc)
        box.append(Gtk.LinkButton(label="Más info", uri=MOCK_URL, halign=Gtk.Align.START))
        card.set_child(box)
        revealer.set_child(card)
        g.add(revealer)

        btn = Gtk.ToggleButton(label="Mostrar / Ocultar", halign=Gtk.Align.START, active=True)
        btn.connect("toggled", lambda b: revealer.set_reveal_child(b.get_active()))
        revealer.connect("notify::reveal-child", lambda r, *a: btn.set_label("Ocultar" if r.get_reveal_child() else "Mostrar"))
        g.add(btn)
        page.add(g)
        return page


class App(Adw.Application):
    def __init__(self):
        super().__init__()
        self.connect("activate", lambda a: Win(a).present())

if __name__ == "__main__":
    App().run()
