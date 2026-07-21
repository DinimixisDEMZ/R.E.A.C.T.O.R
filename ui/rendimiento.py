"""
Pestaña de Rendimiento: Benchmarks manuales, ranking y visualización.
"""

import importlib
from collections.abc import Mapping
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from core.benchmark import correr_benchmark
from core.hybrid import correr_hybrid
from core.scoring import calcular_ranking_manual, calcular_valor_ranking, HYBRID_TYPES
from core.database import guardar_run_completo
from core.operations import OperationCancelled


_TIPOS_MANUALES = (
    "cpu",
    "threads",
    "memory",
    "latencia_fork",
    "latencia_compile",
    "latencia_loaded",
)


def _programar_ui(win, callback, *args):
    programar = getattr(win, "ejecutar_en_ui", None)
    if callable(programar):
        return programar(callback, *args)
    return GLib.idle_add(callback, *args)


def _mostrar_toast(win, mensaje, *, alta=False):
    mostrar = getattr(win, "mostrar_toast", None)
    if callable(mostrar):
        mostrar(mensaje, alta=alta)
        return
    toast = Adw.Toast.new(str(mensaje))
    if alta:
        toast.set_priority(Adw.ToastPriority.HIGH)
    win.toast_overlay.add_toast(toast)


def _mostrar_operacion_ocupada(win):
    mostrar = getattr(win, "mostrar_operacion_ocupada", None)
    if callable(mostrar):
        mostrar()
        return
    state = win.operaciones.state
    nombre = state.name if state is not None else "otra operación"
    _mostrar_toast(
        win,
        f"Operación ocupada: '{nombre}' sigue en curso.",
        alta=True,
    )


def _ejecutar_con_handle(handle, operacion):
    """Ejecuta el worker completo y libera la operación incluso si falla."""
    try:
        return operacion(), None
    except OperationCancelled as exc:
        return None, exc
    except Exception as exc:
        return None, str(exc) or exc.__class__.__name__
    finally:
        handle.release()


def _nueva_generacion_manual(win):
    generacion = int(getattr(win, "_manual_generation", 0) or 0) + 1
    win._manual_generation = generacion
    return generacion


def _resultados_del_modo_actual(win):
    """Excluye resultados sin procedencia o pertenecientes al otro modo."""
    modo_actual = bool(getattr(win, "modo_desarrollador", False))
    resultados = []
    for resultado in getattr(win, "datos_rendimiento", ()) or ():
        if not isinstance(resultado, Mapping):
            continue
        procedencia = resultado.get("development_mode")
        if isinstance(procedencia, bool) and procedencia == modo_actual:
            resultados.append(resultado)
    return resultados


def _correr_y_guardar_benchmark(
    tipo,
    scx_manager,
    log_view,
    modo_desarrollador,
    versiones,
    cancel_token=None,
):
    """Ejecuta una prueba y persiste su run completo en una transacción."""
    if cancel_token is not None:
        cancel_token.raise_if_cancelled()

    engine_kwargs = {"modo_dev": modo_desarrollador}
    if cancel_token is not None:
        engine_kwargs["cancel_token"] = cancel_token

    if tipo in HYBRID_TYPES:
        result = correr_hybrid(
            tipo,
            scx_manager,
            log_view,
            **engine_kwargs,
        )
    else:
        result = correr_benchmark(
            tipo,
            scx_manager,
            log_view,
            **engine_kwargs,
        )

    if result is None:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        return None, None
    if cancel_token is not None and not cancel_token.seal():
        raise OperationCancelled("El benchmark manual fue cancelado.")

    resultado_persistido = dict(result)
    resultado_persistido["development_mode"] = bool(modo_desarrollador)
    run_id = guardar_run_completo(
        versiones,
        [resultado_persistido],
        run_type="manual",
        metadata={"development_mode": bool(modo_desarrollador)},
    )
    resultado_completo = dict(resultado_persistido)
    resultado_completo["run_id"] = run_id
    return resultado_completo, run_id


def _refrescar_historial_publico(win):
    """Invoca perezosamente una API pública de historial si está disponible."""
    callback = getattr(win, "refrescar_historial", None)
    if callable(callback):
        callback()
        return True

    try:
        historial = importlib.import_module("ui.historial")
    except ImportError:
        return False
    callback = getattr(historial, "refrescar_historial", None)
    if callable(callback):
        callback(win)
        return True
    return False


def setup_rendimiento_ui(win):
    """Construye la interfaz de la pestaña Rendimiento.
    
    Args:
        win: Instancia de VentanaSimple
    """
    pref_page = Adw.PreferencesPage()
    win._manual_generation = int(
        getattr(win, "_manual_generation", 0) or 0
    )
    win._manual_development_mode = None

    # ── Ranking General ──
    win.grupo_general = Adw.PreferencesGroup(title="Resultados de Ranking")
    win.fila_lider_manual = Adw.ActionRow(title="Esperando datos...", subtitle="Determina el mejor basado en las pruebas manuales.")
    win.grupo_general.add(win.fila_lider_manual)

    # ── Filas de pruebas (datos inline) ──
    win.filas_pruebas = {}
    win.expanders = {}
    win.expander_rows = {}

    win.grupo_stress = Adw.PreferencesGroup(title="Estrés (stress-ng)")
    for clave, titulo, desc, unidad in [
        ("cpu", "Context Switching", "Respuesta a nuevas tareas", "pts"),
        ("threads", "Carga Mixta", "Uso real de escritorio", "ops/s"),
        ("memory", "Sincronización", "Gestión de bloqueos Mutex", "pts"),
    ]:
        fila = Adw.ActionRow(title=titulo, subtitle=desc)
        lbl_val = Gtk.Label(label="—", css_classes=["dim-label", "monospace"])
        lbl_sched = Gtk.Label(label="", css_classes=["caption"])
        caja = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        caja.append(lbl_sched)
        caja.append(lbl_val)
        fila.add_suffix(caja)
        win.filas_pruebas[clave] = (fila, lbl_val, lbl_sched, unidad)
        win.grupo_stress.add(fila)

    win.grupo_hybrid = Adw.PreferencesGroup(title="Latencia (hyperfine)")
    for clave, titulo, desc, unidad in [
        ("latencia_fork", "Fork+Exec", "Creación de procesos", "µs"),
        ("latencia_compile", "Compilación Paralela", "Throughput real make -j", "µs"),
        ("latencia_loaded", "Bajo Carga", "Foreground saturado", "µs"),
    ]:
        fila = Adw.ActionRow(title=titulo, subtitle=desc)
        lbl_val = Gtk.Label(label="—", css_classes=["dim-label", "monospace"])
        lbl_sched = Gtk.Label(label="", css_classes=["caption"])
        caja = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        caja.append(lbl_sched)
        caja.append(lbl_val)
        fila.add_suffix(caja)
        win.filas_pruebas[clave] = (fila, lbl_val, lbl_sched, unidad)
        win.grupo_hybrid.add(fila)

    # ── Detalle expandible ──
    win.grupo_detalle = Adw.PreferencesGroup(title="Detalle Comparativa")
    for clave, titulo in [
        ("cpu", "Context Switching"),
        ("threads", "Carga Mixta"),
        ("memory", "Sincronización"),
        ("latencia_fork", "Fork+Exec"),
        ("latencia_compile", "Compilación Paralela"),
        ("latencia_loaded", "Bajo Carga"),
    ]:
        exp = Adw.ExpanderRow(title=titulo, subtitle="Expandir para ver ranking completo")
        exp.add_css_class("boxed-list")
        win.expanders[clave] = exp
        win.grupo_detalle.add(exp)

    # ── Consola ──
    grupo_consola = Adw.PreferencesGroup(title="Diagnóstico de Rendimiento")
    win.expander_logs = Adw.ExpanderRow(title="Terminal de Análisis", subtitle="Registro técnico detallado", icon_name="utilities-terminal-symbolic")

    win.text_view_logs = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["card"])
    caja_log = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
    scrolled = Gtk.ScrolledWindow(min_content_height=200, vexpand=True)
    scrolled.set_child(win.text_view_logs)
    caja_log.append(scrolled)
    win.expander_logs.add_row(caja_log)
    grupo_consola.add(win.expander_logs)

    # ── Ensamblar ──
    pref_page.add(win.grupo_general)
    pref_page.add(win.grupo_stress)
    pref_page.add(win.grupo_hybrid)
    pref_page.add(win.grupo_detalle)
    pref_page.add(grupo_consola)

    # ── Header con botones ──
    header = Adw.HeaderBar()
    win.btns_bench = []

    # Izquierda: borrar | stress-ng
    btn_borrar = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Limpiar Rankings")
    btn_borrar.connect("clicked", lambda _button: limpiar_ranking(win))
    header.pack_start(btn_borrar)

    sep_sn = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
    sep_sn.set_margin_start(4)
    sep_sn.set_margin_end(4)
    header.pack_start(sep_sn)

    for icon, tipo, tool in [
        ("org.gnome.Settings-accessibility-pointing-symbolic", "cpu", "Context Switching"),
        ("system-run-symbolic", "threads", "Carga Mixta"),
        ("network-server-symbolic", "memory", "Sincronización")
    ]:
        btn = Gtk.Button(icon_name=icon, tooltip_text=tool, css_classes=["flat"])
        btn.connect("clicked", lambda b, t=tipo: ejecutar_benchmark(win, b, t))
        header.pack_start(btn)
        win.btns_bench.append(btn)

    # Derecha: hyperfine

    for icon, tipo, tool in [
        ("preferences-other-symbolic", "fork", "Fork+Exec"),
        ("utilities-terminal-symbolic", "compile", "Compilación Paralela"),
        ("weather-clear-night-symbolic", "loaded", "Latencia Bajo Carga")
    ]:
        btn = Gtk.Button(icon_name=icon, tooltip_text=tool, css_classes=["flat"])
        btn.connect("clicked", lambda b, t=tipo: ejecutar_benchmark(win, b, t))
        header.pack_end(btn)
        win.btns_bench.append(btn)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_rendimiento.set_child(view)


def ejecutar_benchmark(win, btn, tipo):
    """Inicia una prueba de benchmark individual."""
    if win.en_proceso_bench:
        _mostrar_toast(win, "Ya hay un benchmark manual en curso.")
        return

    handle = win.operaciones.try_acquire(f"benchmark manual ({tipo})")
    if handle is None:
        _mostrar_operacion_ocupada(win)
        return

    icono_original = btn.get_icon_name()
    scx_manager = win.scx
    log_view = win.text_view_logs
    modo_desarrollador = bool(win.modo_desarrollador)
    versiones = dict(win.versiones)
    generacion = int(getattr(win, "_manual_generation", 0) or 0)

    win.en_proceso_bench = True
    btn.set_child(Adw.Spinner())

    for b in win.btns_bench:
        b.set_sensitive(b == btn)
    btn.set_sensitive(False)

    def tarea():
        payload, error = _ejecutar_con_handle(
            handle,
            lambda: _correr_y_guardar_benchmark(
                tipo,
                scx_manager,
                log_view,
                modo_desarrollador,
                versiones,
                cancel_token=handle.token,
            ),
        )
        _programar_ui(
            win,
            _finalizar_benchmark,
            win,
            btn,
            icono_original,
            payload,
            generacion,
            modo_desarrollador,
            error,
        )

    try:
        threading.Thread(target=tarea).start()
    except Exception as exc:
        handle.release()
        finalizar_bench(win, btn, icono_original)
        _mostrar_toast(
            win,
            f"No se pudo iniciar el benchmark: {exc}",
            alta=True,
        )


def _finalizar_benchmark(
    win,
    btn,
    icono_original,
    payload,
    generacion,
    modo_desarrollador,
    error,
):
    try:
        if (
            generacion != getattr(win, "_manual_generation", 0)
            or modo_desarrollador
            != bool(getattr(win, "modo_desarrollador", False))
        ):
            return
        if isinstance(error, OperationCancelled):
            _mostrar_toast(win, "Benchmark manual cancelado.")
            return
        if error is not None:
            _mostrar_toast(win, f"Error en el benchmark: {error}", alta=True)
            return

        result, run_id = payload
        if result is None:
            _mostrar_toast(
                win,
                "El benchmark no produjo un resultado válido.",
                alta=True,
            )
            return

        if (
            not isinstance(result, Mapping)
            or result.get("development_mode") is not modo_desarrollador
            or result.get("run_id") != run_id
        ):
            _mostrar_toast(
                win,
                "El benchmark no conserva una procedencia válida.",
                alta=True,
            )
            return

        win.datos_rendimiento.append(dict(result))
        win._manual_development_mode = modo_desarrollador
        actualizar_interfaz_ranking(win)
        _refrescar_historial_publico(win)
        _mostrar_toast(win, f"Benchmark guardado en el run {run_id}.")
    except Exception as exc:
        _mostrar_toast(
            win,
            f"El benchmark terminó, pero falló la actualización de la UI: {exc}",
            alta=True,
        )
    finally:
        finalizar_bench(win, btn, icono_original)


def finalizar_bench(win, btn=None, icono_original=None):
    """Restaura la UI tras finalizar un benchmark."""
    win.en_proceso_bench = False
    if btn is not None:
        btn.set_child(None)
        if icono_original:
            btn.set_icon_name(icono_original)
    for b in win.btns_bench:
        b.set_sensitive(True)



def actualizar_interfaz_ranking(win):
    """Recalcula y muestra el ranking de pruebas manuales."""
    active_sc = getattr(win, "active_sc", None)
    resultados_actuales = _resultados_del_modo_actual(win)

    for k in _TIPOS_MANUALES:
        fila, lbl_val, lbl_sched, unidad = win.filas_pruebas[k]
        exp = win.expanders[k]

        calc_filt = []
        for d_raw in resultados_actuales:
            if d_raw.get("tipo") == k:
                v_tec = calcular_valor_ranking(d_raw, k)
                d = d_raw.copy()
                d['v_tec'] = v_tec
                calc_filt.append(d)

        # Orden: mayor es mejor para stress-ng, menor para hyperfine
        filt = sorted(calc_filt, key=lambda x: x['v_tec'], reverse=(not k.startswith("latencia_")))

        # Actualizar fila principal
        if filt:
            mejor = filt[0]
            lbl_val.set_text(f"{mejor['v_tec']:,.1f} {unidad}")
            subtitulo = f"#1 {mejor['sched']}"
            if len(filt) > 1:
                subtitulo += f" • {len(filt)} tests"
            lbl_sched.set_text(subtitulo)
            if active_sc and mejor['sched'].lower() == active_sc.lower():
                fila.add_css_class("success")
            else:
                fila.remove_css_class("success")
            fila.set_subtitle(f"{mejor.get('modo', '')} • {len(filt)} prueba(s)")
        else:
            lbl_val.set_text("—")
            lbl_sched.set_text("")
            fila.set_subtitle("Esperando datos...")
            fila.remove_css_class("success")

        # Limpiar filas previas del expander
        for fila_prev in win.expander_rows.get(k, []):
            exp.remove(fila_prev)
        win.expander_rows[k] = []

        # Actualizar expander con detalle
        if filt:
            for i, d in enumerate(filt):
                es_act = active_sc and d['sched'].lower() == active_sc.lower()
                sub = f"{d['v_tec']:,.1f} {unidad} • {d.get('modo', '')}"
                if es_act:
                    sub += " • Actual"
                f_det = Adw.ActionRow(title=f"#{i+1} {d['sched']}", subtitle=sub)
                if i == 0:
                    f_det.add_css_class("success")
                exp.add_row(f_det)
                win.expander_rows[k].append(f_det)
            exp.set_subtitle(f"{len(filt)} resultado(s) disponible(s)")
        else:
            exp.set_subtitle("Sin datos")
            exp.set_expanded(False)

    # ── Calcular líder ──
    win.fila_lider_manual.remove_css_class("success")
    win.fila_lider_manual.remove_css_class("accent")
    # Los run_id persistidos son trazabilidad por prueba, no cohortes de una
    # sesión manual. El scorer recibe la sesión visible sin esos identificadores.
    resultados_scoring = []
    for resultado in resultados_actuales:
        copia = dict(resultado)
        for clave_run in ("run_id", "id_run", "run"):
            copia.pop(clave_run, None)
        resultados_scoring.append(copia)
    scores = calcular_ranking_manual(resultados_scoring)
    if scores:
        lider = max(scores, key=scores.get)
        score_v = scores[lider]
        win.fila_lider_manual.set_title(f"Mejor Planificador: {lider}")
        win.fila_lider_manual.set_subtitle(f"Puntuaci\u00f3n: {score_v:.1f}% (Equilibrio 40/40/20 | Potencia/Respuesta/Fluidez)")
        win.fila_lider_manual.add_css_class("success")
    else:
        win.fila_lider_manual.set_title("Esperando datos...")
        win.fila_lider_manual.set_subtitle(
            "Determina el mejor basado en las pruebas manuales."
        )


def invalidar_estado_rendimiento(win):
    """Descarta resultados, callbacks y procedencia del ranking manual."""
    generacion = _nueva_generacion_manual(win)
    win.datos_rendimiento = []
    win._manual_development_mode = None
    reset_grafico = getattr(getattr(win, "grafico", None), "reset", None)
    if callable(reset_grafico):
        reset_grafico()

    if all(
        hasattr(win, atributo)
        for atributo in (
            "filas_pruebas",
            "expanders",
            "expander_rows",
            "fila_lider_manual",
        )
    ):
        actualizar_interfaz_ranking(win)
        for expander in win.expanders.values():
            expander.set_expanded(False)

    text_view = getattr(win, "text_view_logs", None)
    get_buffer = getattr(text_view, "get_buffer", None)
    if callable(get_buffer):
        get_buffer().set_text("")
    return generacion


def limpiar_ranking(win):
    """Limpia todos los datos de ranking."""
    return invalidar_estado_rendimiento(win)
