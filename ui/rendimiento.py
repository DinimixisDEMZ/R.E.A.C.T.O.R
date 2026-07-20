"""
Pestaña de Rendimiento: Benchmarks manuales, ranking y visualización.
"""

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

from core.benchmark import correr_benchmark
from core.hybrid import correr_hybrid
from core.scoring import calcular_ranking_manual, calcular_valor_grafico, calcular_valor_ranking, _MAPA_CHART, HYBRID_TYPES
from core.database import guardar_run, guardar_resultado
from utils.helpers import obtener_color_css

# Cache de CSS providers para no recrear en cada actualización
_css_providers = {}


def _obtener_o_crear_provider(nombre_sched, clave_ui):
    """Obtiene un CSS provider cacheado o crea uno nuevo."""
    key = f"{nombre_sched}-{clave_ui}"
    if key not in _css_providers:
        provider = Gtk.CssProvider()
        color = obtener_color_css(nombre_sched)
        css = f"progressbar.progress-{key} > trough > progress {{ background-color: {color}; }}"
        provider.load_from_data(css, len(css))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        _css_providers[key] = provider
    return _css_providers[key]


def setup_rendimiento_ui(win):
    """Construye la interfaz de la pestaña Rendimiento.
    
    Args:
        win: Instancia de VentanaSimple
    """
    pref_page = Adw.PreferencesPage()

    def crear_grupo(titulo, reseña):
        grupo = Adw.PreferencesGroup(title=titulo)
        info = Gtk.Image(icon_name="dialog-information-symbolic", css_classes=["dim-label"], margin_end=6)
        info.set_tooltip_text(reseña)
        grupo.set_header_suffix(info)
        lista = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        grupo.add(lista)
        return grupo, lista

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
    for clave, titulo, unidad in [
        ("cpu", "Context Switching", "pts"),
        ("threads", "Carga Mixta", "ops/s"),
        ("memory", "Sincronización", "pts"),
        ("latencia_fork", "Fork+Exec", "µs"),
        ("latencia_compile", "Compilación Paralela", "µs"),
        ("latencia_loaded", "Bajo Carga", "µs"),
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
    btn_borrar.connect("clicked", lambda b: limpiar_ranking(win, b))
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
        return
    win.en_proceso_bench = True

    win._btn_activo = btn
    win._icono_original = btn.get_icon_name()
    btn.set_child(Adw.Spinner())

    for b in win.btns_bench:
        b.set_sensitive(b == btn)
    btn.set_sensitive(False)

    def tarea():
        if tipo in HYBRID_TYPES:
            res = correr_hybrid(tipo, win.scx, win.text_view_logs, modo_dev=win.modo_desarrollador)
        else:
            res = correr_benchmark(tipo, win.scx, win.text_view_logs, modo_dev=win.modo_desarrollador)
        if res:
            GLib.idle_add(_on_resultado, res)
        GLib.idle_add(lambda: finalizar_bench(win))

    def _on_resultado(res):
        win.datos_rendimiento.append(res)
        run_id = guardar_run(win.versiones, run_type="manual")
        guardar_resultado(run_id, res)
        actualizar_interfaz_ranking(win)

    threading.Thread(target=tarea, daemon=True).start()


def finalizar_bench(win):
    """Restaura la UI tras finalizar un benchmark."""
    win.en_proceso_bench = False
    if hasattr(win, '_btn_activo') and win._btn_activo:
        win._btn_activo.set_child(None)
        win._btn_activo.set_icon_name(win._icono_original)
        win._btn_activo = None
    for b in win.btns_bench:
        b.set_sensitive(True)



def actualizar_interfaz_ranking(win):
    """Recalcula y muestra el ranking de pruebas manuales."""
    active_sc = getattr(win, "active_sc", None)

    for k in ["cpu", "threads", "memory", "latencia_fork", "latencia_compile", "latencia_loaded"]:
        fila, lbl_val, lbl_sched, unidad = win.filas_pruebas[k]
        exp = win.expanders[k]

        calc_filt = []
        for d_raw in win.datos_rendimiento:
            if d_raw["tipo"] == k:
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

        # Limpiar filas previas del expander
        for fila_prev in win.expander_rows.get(k, []):
            exp.remove(fila_prev)
        win.expander_rows[k] = []

        # Actualizar expander con detalle
        if filt:
            max_v = filt[0]['v_tec']
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

    # ── Sincronizar gráfica central ──
    for dr in win.datos_rendimiento:
        sc_g = dr["sched"]
        idx_g = _MAPA_CHART.get(dr["tipo"])
        if idx_g is not None:
            val_g = calcular_valor_grafico(dr, dr["tipo"])
            win.grafico.registrar_scheduler(sc_g)
            win.grafico.actualizar_dato(sc_g, idx_g, val_g)

    # ── Calcular líder ──
    scores = calcular_ranking_manual(win.datos_rendimiento)
    if scores:
        lider = max(scores, key=scores.get)
        score_v = scores[lider]
        win.fila_lider_manual.set_title(f"Mejor Planificador: {lider}")
        win.fila_lider_manual.set_subtitle(f"Puntuaci\u00f3n: {score_v:.1f}% (Equilibrio 40/40/20 | Potencia/Respuesta/Fluidez)")


def limpiar_ranking(win, btn):
    """Limpia todos los datos de ranking."""
    win.datos_rendimiento = []
    _css_providers.clear()
    actualizar_interfaz_ranking(win)
