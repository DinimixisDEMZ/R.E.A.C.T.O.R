import importlib.util
from pathlib import Path
import sys
import types

import pytest

from core.operations import OperationCancelled


ROOT = Path(__file__).resolve().parents[1]


class _Widget:
    def __init__(self, *args, **kwargs):
        del args
        self.title = kwargs.get("title")
        self.subtitle = kwargs.get("subtitle")
        self.icon_name = kwargs.get("icon_name")
        self.visible = kwargs.get("visible", True)
        self.sensitive = kwargs.get("sensitive", True)
        self.children = []
        self.css_classes = set(kwargs.get("css_classes", ()))
        self.tooltip = ""
        self._parent = None
        self._child = kwargs.get("content")

    @classmethod
    def new(cls, *args, **kwargs):
        return cls(*args, **kwargs)

    @classmethod
    def new_from_icon_name(cls, icon_name):
        return cls(icon_name=icon_name)

    def add(self, child):
        child._parent = self
        self.children.append(child)

    def add_row(self, child):
        self.add(child)

    def add_suffix(self, child):
        self.add(child)

    def append(self, child):
        self.add(child)

    def remove(self, child):
        self.children.remove(child)
        child._parent = None

    def connect(self, *_args):
        return 1

    def pack_start(self, child):
        self.add(child)

    def pack_end(self, child):
        self.add(child)

    def add_top_bar(self, child):
        self.add(child)

    def set_child(self, child):
        self._child = child

    def get_child(self):
        return self._child

    def get_parent(self):
        return self._parent

    def set_visible(self, visible):
        self.visible = visible

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive

    def set_subtitle(self, subtitle):
        self.subtitle = subtitle

    def set_tooltip_text(self, tooltip):
        self.tooltip = tooltip

    def set_from_icon_name(self, icon_name):
        self.icon_name = icon_name

    def set_opacity(self, _opacity):
        pass

    def set_pixel_size(self, _size):
        pass

    def add_css_class(self, css_class):
        self.css_classes.add(css_class)

    def remove_css_class(self, css_class):
        self.css_classes.discard(css_class)


class _Model:
    def __init__(self, names):
        self.names = list(names)

    def get_n_items(self):
        return len(self.names)

    def get_string(self, index):
        return self.names[index]


@pytest.fixture
def disponibilidad(monkeypatch):
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None
    repository = types.ModuleType("gi.repository")
    repository.Gtk = types.SimpleNamespace(
        Align=types.SimpleNamespace(CENTER=0),
        Orientation=types.SimpleNamespace(VERTICAL=0, HORIZONTAL=1),
        Box=_Widget,
        Button=_Widget,
        Image=_Widget,
        Label=_Widget,
        ScrolledWindow=_Widget,
        Separator=_Widget,
        TextView=_Widget,
    )
    repository.Adw = types.SimpleNamespace(
        ActionRow=_Widget,
        ButtonContent=_Widget,
        ExpanderRow=_Widget,
        HeaderBar=_Widget,
        PreferencesGroup=_Widget,
        PreferencesPage=_Widget,
        Spinner=_Widget,
        Toast=_Widget,
        ToastPriority=types.SimpleNamespace(HIGH=1),
        ToolbarView=_Widget,
    )
    repository.GLib = types.SimpleNamespace(
        idle_add=lambda callback, *args: callback(*args),
        timeout_add=lambda _interval, callback: callback(),
    )
    gi.repository = repository

    helpers = types.ModuleType("utils.helpers")
    helpers.log = lambda *_args, **_kwargs: None
    helpers.limpiar_texto = lambda text: text or ""
    database = types.ModuleType("core.database")
    database.cargar_compatibilidad = (
        lambda _kernel, environment_key=None: {}
    )
    database.limpiar_compatibilidad = lambda: None
    database.obtener_historial_compatibilidad = lambda: []
    database.reemplazar_compatibilidad = (
        lambda _kernel, _results, environment_key=None: None
    )

    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    monkeypatch.setitem(sys.modules, "utils.helpers", helpers)
    monkeypatch.setitem(sys.modules, "core.database", database)

    module_name = "_reactor_test_compatibility_context"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "ui" / "disponibilidad.py",
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def _rows(*names):
    return {
        name: (_Widget(), _Widget(), _Widget())
        for name in names
    }


def _window(names=("scx_lavd",), **kwargs):
    values = {
        "versiones": {"kernel": "6.12-test", "scxctl": "1.0"},
        "modo_desarrollador": False,
        "_disp_generation": 0,
        "_disp_filas": _rows(*names),
        "_disp_grupo_scheds": _Widget(),
        "compatibles": None,
        "scx": types.SimpleNamespace(
            obtener_lista=lambda: pytest.fail("scxctl list en GTK")
        ),
    }
    values.update(kwargs)
    return types.SimpleNamespace(**values)


def _fixed_context(key):
    def capture(_win, names):
        return key, {name: None for name in names}

    return capture


def test_context_hash_tracks_metadata_versions_name_path_and_mode(
    disponibilidad,
    monkeypatch,
):
    state = {
        "paths": {
            "scx_lavd": "/usr/bin/scx_lavd",
            "scx_rusty": "/usr/bin/scx_rusty",
        },
        "realpaths": {
            "/usr/bin/scx_lavd": "/opt/scx/scx_lavd",
            "/usr/bin/scx_rusty": "/opt/scx/scx_rusty",
        },
        "size": 100,
        "mtime_ns": 1_000,
    }
    which_calls = []
    realpath_calls = []
    stat_calls = []

    def which(name):
        which_calls.append(name)
        return state["paths"][name]

    def realpath(path):
        realpath_calls.append(path)
        return state["realpaths"][path]

    def stat(path):
        stat_calls.append(path)
        return types.SimpleNamespace(
            st_size=state["size"],
            st_mtime_ns=state["mtime_ns"],
        )

    monkeypatch.setattr(
        disponibilidad.shutil,
        "which",
        which,
    )
    monkeypatch.setattr(
        disponibilidad.os.path,
        "realpath",
        realpath,
    )
    monkeypatch.setattr(disponibilidad.os, "stat", stat)
    win = _window()

    baseline = disponibilidad.contexto_compatibilidad_actual(
        win,
        ("scx_lavd",),
    )
    assert len(baseline) == 64
    assert which_calls[-1] == "scx_lavd"
    assert realpath_calls[-1] == "/usr/bin/scx_lavd"
    assert stat_calls[-1] == "/opt/scx/scx_lavd"
    assert baseline == disponibilidad.contexto_compatibilidad_actual(
        win,
        ("scx_lavd",),
    )

    state["mtime_ns"] += 1
    assert disponibilidad.contexto_compatibilidad_actual(
        win, ("scx_lavd",)
    ) != baseline
    state["mtime_ns"] -= 1

    state["size"] += 1
    assert disponibilidad.contexto_compatibilidad_actual(
        win, ("scx_lavd",)
    ) != baseline
    state["size"] -= 1

    state["realpaths"]["/usr/bin/scx_lavd"] = "/opt/scx-v2/scx_lavd"
    assert disponibilidad.contexto_compatibilidad_actual(
        win, ("scx_lavd",)
    ) != baseline
    state["realpaths"]["/usr/bin/scx_lavd"] = "/opt/scx/scx_lavd"

    win.versiones["kernel"] = "6.13-test"
    assert disponibilidad.contexto_compatibilidad_actual(
        win, ("scx_lavd",)
    ) != baseline
    win.versiones["kernel"] = "6.12-test"

    win.versiones["scxctl"] = "2.0"
    assert disponibilidad.contexto_compatibilidad_actual(
        win, ("scx_lavd",)
    ) != baseline
    win.versiones["scxctl"] = "1.0"

    win.modo_desarrollador = True
    assert disponibilidad.contexto_compatibilidad_actual(
        win, ("scx_lavd",)
    ) != baseline
    win.modo_desarrollador = False

    assert disponibilidad.contexto_compatibilidad_actual(
        win, ("scx_rusty",)
    ) != baseline


def test_context_missing_binary_is_explicit(disponibilidad, monkeypatch):
    monkeypatch.setattr(disponibilidad.shutil, "which", lambda _name: None)

    assert disponibilidad._identidad_binario_scheduler("lavd") == {
        "scheduler": "lavd",
        "name": "scx_lavd",
        "realpath": None,
        "size": None,
        "mtime_ns": None,
        "missing": True,
    }


def test_binary_check_executes_captured_realpath_with_cancellation(
    disponibilidad,
    monkeypatch,
):
    monkeypatch.setattr(
        disponibilidad.shutil,
        "which",
        lambda _name: pytest.fail("the captured binary must not be resolved again"),
    )
    calls = []

    class Scx:
        def ejecutar_con_sudo(self, command, timeout=None, cancel_token=None):
            calls.append((command, timeout, cancel_token))
            return types.SimpleNamespace(
                returncode=0,
                stdout="scheduler started",
                stderr="",
            )

    token = _Token()
    result = disponibilidad._verificar_binario_bpf(
        Scx(),
        token,
        "scx_lavd",
        "/usr/bin/timeout",
        object(),
        "/opt/scx/scx_lavd",
    )

    assert result == (True, "Disponible (Arranque verificado)", False)
    assert calls == [
        (
            [
                "/usr/bin/timeout",
                "-k",
                "1",
                "5",
                "/opt/scx/scx_lavd",
            ],
            8,
            token,
        )
    ]


def test_setup_rejects_legacy_cache_and_never_lists_scx(
    disponibilidad,
    monkeypatch,
):
    calls = []
    invalidated = []

    def load(kernel, environment_key=None):
        calls.append((kernel, environment_key))
        if environment_key is None:
            return {"scx_lavd": (True, "legacy", 1.0)}
        return {}

    monkeypatch.setattr(disponibilidad, "cargar_compatibilidad", load)
    monkeypatch.setattr(
        disponibilidad,
        "_capturar_contexto_compatibilidad",
        _fixed_context("context-key"),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )
    automation = types.ModuleType("ui.automatizacion")

    def invalidate(win):
        invalidated.append(True)
        win._auto_sched_checks.clear()

    automation._invalidar_auto_schedulers = invalidate
    monkeypatch.setitem(sys.modules, "ui.automatizacion", automation)
    win = _window(
        modelo_schedulers=_Model(("scx_lavd",)),
        pag_disponibilidad=_Widget(),
        compatibles=["scx_lavd"],
        _auto_sched_checks={"legacy": object()},
    )

    disponibilidad.setup_disponibilidad_ui(win)

    assert calls == [("6.12-test", "context-key")]
    assert win._compatibility_context == "context-key"
    assert win.compatibles is None
    assert invalidated == [True]
    assert win._auto_sched_checks == {}
    assert win._disp_filas["scx_lavd"][0].subtitle == "Sin verificar"


def test_empty_history_fallback_never_lists_scx(disponibilidad):
    win = _window(
        (),
        modelo_schedulers=_Model(()),
        _disp_grupo_historial=None,
        _disp_grupo_logs=_Widget(),
        _disp_pref_page=_Widget(),
    )

    disponibilidad._refrescar_historial_compat(win)

    assert win._disp_grupo_historial is not None


def test_exact_all_false_snapshot_remains_verified(
    disponibilidad,
    monkeypatch,
):
    cache = {
        "scx_lavd": (False, "incompatible", 1.0),
        "scx_rusty": (False, "incompatible", 1.0),
    }
    calls = []
    monkeypatch.setattr(
        disponibilidad,
        "_capturar_contexto_compatibilidad",
        _fixed_context("context-key"),
    )
    monkeypatch.setattr(
        disponibilidad,
        "cargar_compatibilidad",
        lambda kernel, environment_key=None: calls.append(
            (kernel, environment_key)
        )
        or cache,
    )
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )
    win = _window(
        ("scx_lavd", "scx_rusty"),
        modelo_schedulers=_Model(("scx_lavd", "scx_rusty")),
    )

    disponibilidad.recargar_disponibilidad_ui(win)

    assert calls == [("6.12-test", "context-key")]
    assert win.compatibles == []
    assert win._compatibility_context == "context-key"
    assert all(
        icon.icon_name == "dialog-error-symbolic"
        for _row, _spinner, icon in win._disp_filas.values()
    )


def test_context_change_invalidates_previous_snapshot_immediately(
    disponibilidad,
    monkeypatch,
):
    state = {"key": "old-key"}
    snapshots = {
        "old-key": {"scx_lavd": (True, "ok", 1.0)},
        "new-key": {},
    }
    monkeypatch.setattr(
        disponibilidad,
        "_capturar_contexto_compatibilidad",
        lambda _win, names: (
            state["key"],
            {name: None for name in names},
        ),
    )
    monkeypatch.setattr(
        disponibilidad,
        "cargar_compatibilidad",
        lambda _kernel, environment_key=None: snapshots[environment_key],
    )
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )
    win = _window()

    disponibilidad.recargar_disponibilidad_ui(win, ("scx_lavd",))
    assert win.compatibles == ["scx_lavd"]

    state["key"] = "new-key"
    disponibilidad.recargar_disponibilidad_ui(win, ("scx_lavd",))

    assert win._compatibility_context == "new-key"
    assert win.compatibles is None
    assert win._disp_filas["scx_lavd"][0].subtitle == "Sin verificar"


def test_context_change_during_database_load_never_publishes_stale_cache(
    disponibilidad,
    monkeypatch,
):
    captures = iter(("old-key", "new-key", "new-key", "new-key"))
    loads = []

    def capture(_win, names):
        key = next(captures)
        return key, {name: f"/{key}/{name}" for name in names}

    def load(_kernel, environment_key=None):
        loads.append(environment_key)
        if environment_key == "old-key":
            return {"scx_lavd": (True, "stale", 1.0)}
        return {}

    monkeypatch.setattr(
        disponibilidad,
        "_capturar_contexto_compatibilidad",
        capture,
    )
    monkeypatch.setattr(disponibilidad, "cargar_compatibilidad", load)
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )
    win = _window()

    disponibilidad.recargar_disponibilidad_ui(win, ("scx_lavd",))

    assert loads == ["old-key", "new-key"]
    assert win._compatibility_context == "new-key"
    assert win.compatibles is None
    assert win._disp_filas["scx_lavd"][0].subtitle == "Sin verificar"


def test_clear_cache_clears_context_compatibles_and_checklist(
    disponibilidad,
    monkeypatch,
):
    cleared = []
    invalidated = []
    automation = types.ModuleType("ui.automatizacion")

    def invalidate(win):
        invalidated.append(True)
        win._auto_sched_checks.clear()

    automation._invalidar_auto_schedulers = invalidate
    monkeypatch.setitem(sys.modules, "ui.automatizacion", automation)
    monkeypatch.setattr(
        disponibilidad,
        "limpiar_compatibilidad",
        lambda: cleared.append(True),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )
    image = _Widget()
    nav_child = types.SimpleNamespace(get_first_child=lambda: image)
    nav = _Widget()
    nav.set_child(nav_child)
    win = _window(
        _verificando=False,
        _compatibility_context="old-key",
        compatibles=["scx_lavd"],
        _auto_sched_checks={"scx_lavd": object()},
        nav_disponibilidad=nav,
        text_view_logs_disp=object(),
    )

    disponibilidad._limpiar_cache(win)

    assert cleared == [True]
    assert invalidated == [True]
    assert win._compatibility_context is None
    assert win.compatibles is None
    assert win._auto_sched_checks == {}


class _Token:
    def __init__(self):
        self.cancelled = False
        self.sealed = False

    def raise_if_cancelled(self):
        if self.cancelled:
            raise OperationCancelled("cancelled")

    def cancel(self):
        if not self.sealed:
            self.cancelled = True

    def seal(self):
        if self.cancelled:
            return False
        self.sealed = True
        return True


class _Handle:
    def __init__(self):
        self.token = _Token()
        self.released = False

    def check_cancelled(self):
        self.token.raise_if_cancelled()

    def release(self):
        self.released = True


class _Session:
    def __init__(self, token, applied, cancel_on_exit=False):
        self.token = token
        self.applied = applied
        self.cancel_on_exit = cancel_on_exit
        self.restore_error = None

    def __enter__(self):
        return self

    def aplicar(self, state):
        self.applied.append(state)

    def __exit__(self, *_args):
        if self.cancel_on_exit:
            self.token.cancel()
        return False


class _Scx:
    def __init__(self, applied, cancel_on_exit=False):
        self.applied = applied
        self.cancel_on_exit = cancel_on_exit

    def sesion(self, token):
        return _Session(token, self.applied, self.cancel_on_exit)


def _worker_window(disponibilidad, names, context, compatibles):
    return _window(
        names,
        modo_desarrollador=True,
        _disp_generation=4,
        _compatibility_context=context,
        compatibles=list(compatibles),
    )


def test_worker_applies_base_before_each_test_and_replaces_with_context(
    disponibilidad,
    monkeypatch,
):
    monkeypatch.setattr(disponibilidad.shutil, "which", lambda _name: None)
    names = ("scx_lavd", "scx_rusty")
    win = _window(
        names,
        modo_desarrollador=True,
        _disp_generation=4,
        _verificando=True,
        _btn_verificar_disp=_Widget(),
        _btn_limpiar_disp=_Widget(),
        mostrar_toast=lambda *_args, **_kwargs: None,
    )
    context = disponibilidad.contexto_compatibilidad_actual(win, names)
    win._compatibility_context = context
    handle = _Handle()
    applied = []
    replacements = []
    callbacks = []
    monkeypatch.setattr(
        disponibilidad,
        "reemplazar_compatibilidad",
        lambda kernel, results, environment_key=None: replacements.append(
            (kernel, results, environment_key)
        ),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_programar_ui",
        lambda _win, callback, *args: callbacks.append((callback, args)),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_actualizar_badge_compatibilidad",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )

    disponibilidad._worker_verificacion(
        win,
        handle,
        _Scx(applied),
        tuple((name, *win._disp_filas[name]) for name in names),
        True,
        "6.12-test",
        object(),
        generacion=4,
        contexto=context,
        nombres_snapshot=names,
    )

    assert handle.released is True
    assert len(applied) == 2
    assert all(state.sistema_base for state in applied)
    assert replacements == [
        (
            "6.12-test",
            tuple(
                (
                    name,
                    disponibilidad._compatibilidad_dev_determinista(name),
                    (
                        "Disponible (Simulado determinista)"
                        if disponibilidad._compatibilidad_dev_determinista(name)
                        else "Programa incompatible (Simulado determinista)"
                    ),
                )
                for name in names
            ),
            context,
        )
    ]
    assert callbacks[-1][0] is disponibilidad._finalizar_verificacion
    assert callbacks[-1][1][2] is True


def test_cancelled_worker_preserves_exact_previous_snapshot(
    disponibilidad,
    monkeypatch,
):
    monkeypatch.setattr(disponibilidad.shutil, "which", lambda _name: None)
    names = ("scx_lavd",)
    win = _window(
        names,
        modo_desarrollador=True,
        _disp_generation=4,
        _verificando=True,
        _btn_verificar_disp=_Widget(),
        _btn_limpiar_disp=_Widget(),
        mostrar_toast=lambda *_args, **_kwargs: None,
    )
    context = disponibilidad.contexto_compatibilidad_actual(win, names)
    win._compatibility_context = context
    previous = {"scx_lavd": (False, "previous", 10.0)}
    handle = _Handle()
    replacements = []
    callbacks = []
    monkeypatch.setattr(
        disponibilidad,
        "reemplazar_compatibilidad",
        lambda *args, **kwargs: replacements.append((args, kwargs)),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_programar_ui",
        lambda _win, callback, *args: callbacks.append((callback, args)),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_actualizar_badge_compatibilidad",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )

    disponibilidad._worker_verificacion(
        win,
        handle,
        _Scx([], cancel_on_exit=True),
        (("scx_lavd", object(), object(), object()),),
        True,
        "6.12-test",
        object(),
        previous,
        [],
        True,
        4,
        context,
        names,
    )

    assert replacements == []
    callback, args = callbacks[-1]
    assert callback is disponibilidad._finalizar_verificacion
    assert args[2] is False
    assert args[3] is True
    assert args[5] == previous
    assert args[6] == []
    assert args[7] is True
    assert args[10] == context

    callback(*args)
    row, spinner, icon = win._disp_filas["scx_lavd"]
    assert win.compatibles == []
    assert row.subtitle == "previous"
    assert spinner.visible is False
    assert icon.icon_name == "dialog-error-symbolic"


def test_cancellation_between_candidates_stops_without_persisting(
    disponibilidad,
    monkeypatch,
):
    monkeypatch.setattr(disponibilidad.shutil, "which", lambda _name: None)
    names = ("scx_lavd", "scx_rusty")
    win = _window(names, modo_desarrollador=True, _disp_generation=4)
    context = disponibilidad.contexto_compatibilidad_actual(win, names)
    win._compatibility_context = context
    handle = _Handle()
    applied = []
    replacements = []
    callbacks = []

    def schedule(_win, callback, *args):
        callbacks.append((callback, args))
        if (
            callback is disponibilidad._aplicar_ui_verificacion_si_vigente
            and args[5] is disponibilidad._actualizar_fila
        ):
            handle.token.cancel()

    monkeypatch.setattr(disponibilidad, "_programar_ui", schedule)
    monkeypatch.setattr(
        disponibilidad,
        "reemplazar_compatibilidad",
        lambda *args, **kwargs: replacements.append((args, kwargs)),
    )

    disponibilidad._worker_verificacion(
        win,
        handle,
        _Scx(applied),
        tuple((name, object(), object(), object()) for name in names),
        True,
        "6.12-test",
        object(),
        generacion=4,
        contexto=context,
        nombres_snapshot=names,
    )

    assert replacements == []
    assert len(applied) == 1
    callback, args = callbacks[-1]
    assert callback is disponibilidad._finalizar_verificacion
    assert args[2] is False
    assert args[3] is True


def test_external_binary_change_between_candidates_does_not_mutate_snapshot(
    disponibilidad,
    monkeypatch,
):
    state = {"mtime_ns": 100}
    monkeypatch.setattr(
        disponibilidad.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(disponibilidad.os.path, "realpath", lambda path: path)
    monkeypatch.setattr(
        disponibilidad.os,
        "stat",
        lambda _path: types.SimpleNamespace(
            st_size=100,
            st_mtime_ns=state["mtime_ns"],
        ),
    )
    names = ("scx_lavd", "scx_rusty")
    previous = {
        "scx_lavd": (True, "previous", 10.0),
        "scx_rusty": (False, "previous", 10.0),
    }
    win = _window(
        names,
        modo_desarrollador=True,
        _disp_generation=4,
        compatibles=["scx_lavd"],
        _verificando=True,
        _btn_verificar_disp=_Widget(),
        _btn_limpiar_disp=_Widget(),
        mostrar_toast=lambda *_args, **_kwargs: None,
    )
    context = disponibilidad.contexto_compatibilidad_actual(win, names)
    win._compatibility_context = context
    handle = _Handle()
    applied = []
    replacements = []
    callbacks = []

    def schedule(_win, callback, *args):
        callbacks.append((callback, args))
        if (
            callback is disponibilidad._aplicar_ui_verificacion_si_vigente
            and args[5] is disponibilidad._actualizar_fila
        ):
            state["mtime_ns"] += 1

    monkeypatch.setattr(disponibilidad, "_programar_ui", schedule)
    monkeypatch.setattr(
        disponibilidad,
        "reemplazar_compatibilidad",
        lambda *args, **kwargs: replacements.append((args, kwargs)),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )

    disponibilidad._worker_verificacion(
        win,
        handle,
        _Scx(applied),
        tuple((name, *win._disp_filas[name]) for name in names),
        True,
        "6.12-test",
        object(),
        previous,
        ["scx_lavd"],
        True,
        4,
        context,
        names,
    )

    assert replacements == []
    assert len(applied) == 1
    assert win.compatibles == ["scx_lavd"]
    stale_callback, stale_args = next(
        (queued_callback, queued_args)
        for queued_callback, queued_args in callbacks
        if (
            queued_callback
            is disponibilidad._aplicar_ui_verificacion_si_vigente
            and queued_args[5] is disponibilidad._actualizar_fila
        )
    )
    stale_callback(*stale_args)
    assert win._disp_filas["scx_lavd"][0].subtitle is None
    callback, args = callbacks[-1]
    assert callback is disponibilidad._finalizar_verificacion
    assert args[2] is False
    assert args[3] is False
    assert "contexto" in args[4]
    assert args[5] == previous
    assert args[6] == ["scx_lavd"]

    callback(*args)
    assert win.compatibles is None
    assert win._compatibility_context != context
    assert win._disp_filas["scx_lavd"][0].subtitle == "Sin verificar"
