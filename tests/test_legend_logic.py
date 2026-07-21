import importlib.util
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]


class FakeWidget:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.opacity = 1.0
        self.css_classes = list(kwargs.get("css_classes", []))
        self.children = []

    def set_cursor(self, cursor):
        self.cursor = cursor

    def set_opacity(self, opacity):
        self.opacity = opacity

    def set_tooltip_text(self, text):
        self.tooltip = text

    def update_property(self, properties, values):
        self.accessible_properties = dict(zip(properties, values))

    def set_child(self, child):
        self.child = child

    def set_content_width(self, width):
        self.content_width = width

    def set_content_height(self, height):
        self.content_height = height

    def set_valign(self, align):
        self.valign = align

    def set_margin_start(self, margin):
        self.margin_start = margin

    def set_margin_end(self, margin):
        self.margin_end = margin

    def set_margin_top(self, margin):
        self.margin_top = margin

    def set_margin_bottom(self, margin):
        self.margin_bottom = margin

    def set_draw_func(self, draw_func, user_data):
        self.draw_func = draw_func
        self.draw_user_data = user_data

    def add_css_class(self, css_class):
        self.css_classes.append(css_class)


class FakeBox(FakeWidget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def append(self, child):
        self.children.append(child)


class FakeToggleButton(FakeWidget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.active = False
        self._signals = {}

    def connect(self, signal, callback, *user_data):
        self._signals[signal] = (callback, user_data)

    def set_active(self, active):
        changed = self.active != bool(active)
        self.active = bool(active)
        if changed and "toggled" in self._signals:
            callback, user_data = self._signals["toggled"]
            callback(self, *user_data)

    def get_active(self):
        return self.active

    def toggle(self):
        self.set_active(not self.active)


class FakeDrawingArea(FakeWidget):
    pass


class FakeLabel(FakeWidget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.label = kwargs.get("label")


class FakeCursor:
    @classmethod
    def new_from_name(cls, name, fallback):
        return cls(name, fallback)

    def __init__(self, name, fallback):
        self.name = name
        self.fallback = fallback


@pytest.fixture
def legend(monkeypatch):
    gtk = types.SimpleNamespace(
        ToggleButton=FakeToggleButton,
        Box=FakeBox,
        DrawingArea=FakeDrawingArea,
        Label=FakeLabel,
        Align=types.SimpleNamespace(CENTER="center"),
        AccessibleProperty=types.SimpleNamespace(LABEL="label"),
    )
    gdk = types.SimpleNamespace(Cursor=FakeCursor)
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None
    repository = types.ModuleType("gi.repository")
    repository.Gtk = gtk
    repository.Gdk = gdk
    gi.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    spec = importlib.util.spec_from_file_location(
        "_reactor_test_legend_logic",
        ROOT / "widgets" / "legend.py",
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


class FakeGraph:
    colores = {"scx_lavd": (0.1, 0.2, 0.3)}

    def __init__(self, ocultos):
        self.ocultos = set(ocultos)
        self.draws = 0

    def queue_draw(self):
        self.draws += 1


def test_calcular_estado_leyenda_es_puro_y_conserva_el_conjunto(legend):
    ocultos = {"scx_other"}

    estado = legend.calcular_estado_leyenda("scx_lavd", ocultos, False)

    assert estado[:3] == (False, 0.4, 0.3)
    assert estado[3] == {"scx_other", "scx_lavd"}
    assert estado[3] is not ocultos
    assert ocultos == {"scx_other"}

    visible = legend.calcular_estado_leyenda("scx_lavd", estado[3], True)
    assert visible[:3] == (True, 1.0, 1.0)
    assert visible[3] == {"scx_other"}


def test_chip_es_toggle_accesible_y_sincroniza_grafico(legend):
    grafico = FakeGraph({"scx_lavd"})
    contenedor = FakeBox()

    chip = legend.crear_chip_leyenda("scx_lavd", grafico, contenedor)

    assert isinstance(chip, FakeToggleButton)
    assert chip in contenedor.children
    assert chip.css_classes == ["card", "pill"]
    assert chip.get_active() is False
    assert chip.opacity == 0.4
    assert chip.child.children[0].opacity == 0.3
    assert chip.tooltip == "Mostrar scheduler scx_lavd"
    assert chip.accessible_properties["label"] == "Mostrar scheduler scx_lavd"

    chip.toggle()

    assert chip.get_active() is True
    assert grafico.ocultos == set()
    assert chip.opacity == 1.0
    assert chip.child.children[0].opacity == 1.0
    assert chip.tooltip == "Ocultar scheduler scx_lavd"
    assert chip.accessible_properties["label"] == "Ocultar scheduler scx_lavd"
    assert grafico.draws == 1
