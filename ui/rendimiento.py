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
from core.scoring import calcular_ranking_manual
from core.tipos import MAPA_CHART, valor_para_grafico, valor_para_ranking, claves_hibridas
from core.database import guardar_run, guardar_resultado
from ui.info_pruebas import mostrar_info_grupo
from utils.i18n import traducir


def configurar_ui_rendimiento(win):
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
    win.grupo_general = Adw.PreferencesGroup(title=traducir("Resultados de Ranking"))
    win.fila_lider_manual = Adw.ActionRow(title=traducir("Esperando datos..."), subtitle=traducir("Determina el mejor basado en las pruebas manuales."))
    win.grupo_general.add(win.fila_lider_manual)

    # ── Filas de pruebas (datos inline) ──
    win.filas_pruebas = {}
    win.expanders = {}
    win.expander_rows = {}

    win.grupo_stress = Adw.PreferencesGroup(title=traducir("Estrés (stress-ng)"))
    btn_info_stress = Gtk.Button(icon_name="dialog-information-symbolic", css_classes=["flat", "circular"], tooltip_text=traducir("Info sobre estas pruebas"))
    btn_info_stress.connect("clicked", lambda b: mostrar_info_grupo(win, ["cpu", "threads", "memory"], traducir("Estrés (stress-ng)")))
    win.grupo_stress.set_header_suffix(btn_info_stress)
    win.btns_bench = []
    for icono, clave, titulo, desc, unidad in [
        ("input-mouse-symbolic", "cpu", traducir("Cambio de Contexto"), traducir("Respuesta a nuevas tareas"), "pts"),
        ("system-run-symbolic", "threads", traducir("Carga Mixta"), traducir("Uso real de escritorio"), "ops/s"),
        ("network-server-symbolic", "memory", traducir("Sincronización"), traducir("Gestión de bloqueos Mutex"), "pts"),
    ]:
        fila = Adw.ActionRow(title=titulo, subtitle=desc)
        lbl_val = Gtk.Label(label="—", css_classes=["dim-label", "monospace"])
        lbl_sched = Gtk.Label(label="", css_classes=["caption"])
        btn_play = Gtk.Button(icon_name=icono, css_classes=["flat", "circular"],
                              valign=Gtk.Align.CENTER, tooltip_text=traducir("Ejecutar prueba"))
        btn_play.connect("clicked", lambda b, t=clave: ejecutar_benchmark(win, b, t))
        caja = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        caja.append(lbl_sched)
        caja.append(lbl_val)
        caja.append(btn_play)
        fila.add_suffix(caja)
        win.filas_pruebas[clave] = (fila, lbl_val, lbl_sched, unidad)
        win.btns_bench.append(btn_play)
        win.grupo_stress.add(fila)

    win.grupo_hybrid = Adw.PreferencesGroup(title=traducir("Latencia (hyperfine)"))
    btn_info_hybrid = Gtk.Button(icon_name="dialog-information-symbolic", css_classes=["flat", "circular"], tooltip_text=traducir("Info sobre estas pruebas"))
    btn_info_hybrid.connect("clicked", lambda b: mostrar_info_grupo(win, ["fork", "compile", "loaded"], traducir("Latencia (hyperfine)")))
    win.grupo_hybrid.set_header_suffix(btn_info_hybrid)
    for icono, clave, titulo, desc, unidad in [
        ("preferences-other-symbolic", "latencia_fork", traducir("Creación de Procesos"), traducir("Creación de procesos"), "µs"),
        ("utilities-terminal-symbolic", "latencia_compile", traducir("Compilación Paralela"), traducir("hyperfine + rt-tests"), "µs"),
        ("weather-clear-night-symbolic", "latencia_loaded", traducir("Bajo Carga"), traducir("Primer plano saturado"), "µs"),
    ]:
        fila = Adw.ActionRow(title=titulo, subtitle=desc)
        lbl_val = Gtk.Label(label="—", css_classes=["dim-label", "monospace"])
        lbl_sched = Gtk.Label(label="", css_classes=["caption"])
        btn_play = Gtk.Button(icon_name=icono, css_classes=["flat", "circular"],
                              valign=Gtk.Align.CENTER, tooltip_text=traducir("Ejecutar prueba"))
        btn_play.connect("clicked", lambda b, t=clave: ejecutar_benchmark(win, b, t))
        caja = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        caja.append(lbl_sched)
        caja.append(lbl_val)
        caja.append(btn_play)
        fila.add_suffix(caja)
        win.filas_pruebas[clave] = (fila, lbl_val, lbl_sched, unidad)
        win.btns_bench.append(btn_play)
        win.grupo_hybrid.add(fila)

    # ── Detalle expandible ──
    win.grupo_detalle = Adw.PreferencesGroup(title=traducir("Detalle Comparativa"))
    for clave, titulo, unidad in [
        ("cpu", traducir("Cambio de Contexto"), "pts"),
        ("threads", traducir("Carga Mixta"), "ops/s"),
        ("memory", traducir("Sincronización"), "pts"),
        ("latencia_fork", traducir("Creación de Procesos"), "µs"),
        ("latencia_compile", traducir("Compilación Paralela"), "µs"),
        ("latencia_loaded", traducir("Bajo Carga"), "µs"),
    ]:
        exp = Adw.ExpanderRow(title=titulo, subtitle=traducir("Expandir para ver ranking completo"))
        exp.add_css_class("boxed-list")
        win.expanders[clave] = exp
        win.grupo_detalle.add(exp)

    # ── Consola ──
    grupo_consola = Adw.PreferencesGroup(title=traducir("Diagnóstico de Rendimiento"))
    win.text_view_logs = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["card"])
    win._dialog_logs = None

    def _abrir_logs():
        if win._dialog_logs is None:
            scrolled = Gtk.ScrolledWindow(min_content_height=400, vexpand=True)
            scrolled.set_child(win.text_view_logs)
            win._dialog_logs = Adw.Dialog()
            ancho = win.get_width()
            win._dialog_logs.set_content_width(max(ancho - 40, 400))
            win._dialog_logs.set_content_height(500)
            win._dialog_logs.set_presentation_mode(Adw.DialogPresentationMode.BOTTOM_SHEET)
            win._dialog_logs.set_child(scrolled)
        win._dialog_logs.present(win)

    btn_logs = Adw.ActionRow(
        title=traducir("Terminal de Análisis"),
        subtitle=traducir("Registro técnico detallado"),
        icon_name="utilities-terminal-symbolic",
    )
    btn_logs.set_activatable(True)
    btn_logs.connect("activated", lambda *_: _abrir_logs())
    grupo_consola.add(btn_logs)

    # ── Ensamblar ──
    pref_page.add(win.grupo_general)
    pref_page.add(win.grupo_stress)
    pref_page.add(win.grupo_hybrid)
    pref_page.add(win.grupo_detalle)
    pref_page.add(grupo_consola)

    # ── Header — solo papelera ──
    header = Adw.HeaderBar()
    btn_borrar = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=traducir("Limpiar Rankings"))
    btn_borrar.connect("clicked", lambda b: limpiar_ranking(win, b))
    header.pack_start(btn_borrar)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_rendimiento.set_child(view)


def ejecutar_benchmark(win, btn, tipo):
    """Inicia una prueba de benchmark individual."""
    if win.bench_state.en_proceso:
        return
    win.bench_state.en_proceso = True
    win.bench_state.btn_activo = btn
    win.bench_state.icono_original = btn.get_icon_name()
    spinner = Gtk.Spinner()
    spinner.set_size_request(16, 16)
    spinner.start()
    btn.set_child(spinner)

    for b in win.btns_bench:
        b.set_sensitive(b == btn)
    btn.set_sensitive(False)

    _MAPA_ALIAS = {"latencia_fork": "fork", "latencia_compile": "compile", "latencia_loaded": "loaded"}

    def tarea():
        if tipo in claves_hibridas():
            res = correr_hybrid(_MAPA_ALIAS.get(tipo, tipo), win.scx, win.text_view_logs, modo_dev=win.modo_desarrollador)
        else:
            res = correr_benchmark(tipo, win.scx, win.text_view_logs, modo_dev=win.modo_desarrollador)
        if res:
            GLib.idle_add(_on_resultado, res)
        GLib.idle_add(lambda: finalizar_bench(win))

    def _on_resultado(res):
        win.bench_state.datos.append(res)
        run_id = guardar_run(win.versiones, run_type="manual")
        guardar_resultado(run_id, res)
        actualizar_interfaz_ranking(win)

    threading.Thread(target=tarea, daemon=True).start()


def finalizar_bench(win):
    """Restaura la UI tras finalizar un benchmark."""
    win.bench_state.en_proceso = False
    if win.bench_state.btn_activo:
        win.bench_state.btn_activo.set_child(None)
        win.bench_state.btn_activo.set_icon_name(win.bench_state.icono_original)
        win.bench_state.btn_activo = None
    for b in win.btns_bench:
        b.set_sensitive(True)



def actualizar_interfaz_ranking(win):
    """Recalcula y muestra el ranking de pruebas manuales."""
    active_sc = getattr(win, "active_sc", None)

    for k in ["cpu", "threads", "memory", "latencia_fork", "latencia_compile", "latencia_loaded"]:
        fila, lbl_val, lbl_sched, unidad = win.filas_pruebas[k]
        exp = win.expanders[k]

        calc_filt = []
        for d_raw in win.bench_state.datos:
            if d_raw["tipo"] == k:
                v_tec = valor_para_ranking(d_raw, k)
                d = d_raw.copy()
                d['v_tec'] = v_tec
                calc_filt.append(d)

        # Orden: mayor es mejor para stress-ng, menor para hyperfine
        filt = sorted(calc_filt, key=lambda x: x['v_tec'], reverse=(not k.startswith("latencia_")))

        # Actualizar fila principal
        if filt:
            mejor = filt[0]
            lbl_val.set_text(f"{mejor['v_tec']:,.1f} {unidad}")
            subtitulo = traducir("#1 {}").format(mejor['sched'])
            if len(filt) > 1:
                subtitulo += traducir(" • {} tests").format(len(filt))
            lbl_sched.set_text(subtitulo)
            if active_sc and mejor['sched'].lower() == active_sc.lower():
                fila.add_css_class("success")
            else:
                fila.remove_css_class("success")
            fila.set_subtitle(traducir("{} • {} prueba(s)").format(mejor.get('modo', ''), len(filt)))
        else:
            lbl_val.set_text("—")
            lbl_sched.set_text("")
            fila.set_subtitle(traducir("Esperando datos..."))

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
                    sub += " • " + traducir("Actual")
                f_det = Adw.ActionRow(title=f"#{i+1} {d['sched']}", subtitle=sub)
                if i == 0:
                    f_det.add_css_class("success")
                exp.add_row(f_det)
                win.expander_rows[k].append(f_det)
            exp.set_subtitle(traducir("{} resultado(s) disponible(s)").format(len(filt)))
        else:
            exp.set_subtitle(traducir("Sin datos"))

    # ── Sincronizar gráfica central ──
    for dr in win.bench_state.datos:
        sc_g = dr["sched"]
        idx_g = MAPA_CHART.get(dr["tipo"])
        if idx_g is not None:
            val_g = valor_para_grafico(dr, dr["tipo"])
            win.grafico.registrar_scheduler(sc_g)
            win.grafico.actualizar_dato(sc_g, idx_g, val_g)

    # ── Calcular líder ──
    scores = calcular_ranking_manual(win.bench_state.datos)
    if scores:
        lider = max(scores, key=scores.get)
        score_v = scores[lider]
        win.fila_lider_manual.set_title(traducir("Mejor Planificador: {}").format(lider))
        win.fila_lider_manual.set_subtitle(traducir("Puntuación: {:.1f}% (Equilibrio 45/45/10 | Potencia/Respuesta/Fluidez)").format(score_v))


def limpiar_ranking(win, btn):
    """Limpia todos los datos de ranking."""
    win.bench_state.datos = []
    actualizar_interfaz_ranking(win)
