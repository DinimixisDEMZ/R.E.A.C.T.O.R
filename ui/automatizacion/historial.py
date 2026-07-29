"""
Navegación de historial de runs automáticos: popover, selección y carga.
"""

import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk

from utils.i18n import traducir
from core.scoring import calcular_scores_finales
from core.tipos import MAPA_CHART, valor_para_grafico
from core.database import consultar_runs_auto, cargar_resultados_de_run
from widgets.legend import crear_chip_leyenda


def reconstruir_brutos(resultados):
    """Convierte lista plana de resultados DB en dict {sched: {tipo: {valor, p95, waste, modo}}}."""
    brutos = {}
    for r in resultados:
        sc = r["scheduler_name"]
        tt = r["test_type"]
        if sc not in brutos:
            brutos[sc] = {}
        brutos[sc][tt] = {
            "tipo": tt,
            "valor": r["valor"],
            "p95": r["p95"] or 1.0,
            "waste": r["waste"] or 0.5,
            "sched": sc,
            "modo": r.get("modo", "auto"),
        }
    return brutos


def refrescar_historial(win):
    """Recarga la lista de runs desde la BD y reconstruye el popover."""
    runs = consultar_runs_auto()
    win.auto_state.historial_runs = runs

    if not runs:
        win.auto_state.indice_historial = -1
        win.btn_hist.set_label(traducir("Sin historial"))
        win.btn_hist.set_sensitive(False)
        vaciar_popover(win)
        return

    if win.auto_state.indice_historial < 0 or win.auto_state.indice_historial >= len(runs):
        win.auto_state.indice_historial = len(runs) - 1
    elif win.auto_state.indice_historial == len(runs) - 2 and len(runs) > 0:
        win.auto_state.indice_historial = len(runs) - 1

    actualizar_popover(win)
    actualizar_botones_nav(win)


def vaciar_popover(win):
    """Limpia el contenido del popover de historial."""
    popover = getattr(win, "popover_hist", None)
    if popover is not None:
        popover.set_child(None)


def actualizar_popover(win):
    """Construye la lista de runs en el popover."""
    runs = win.auto_state.historial_runs
    idx = win.auto_state.indice_historial

    scroll = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vexpand=True,
    )
    listbox = Gtk.ListBox(
        css_classes=["boxed-list"],
        selection_mode=Gtk.SelectionMode.SINGLE,
    )

    for i, run in enumerate(runs):
        row = _crear_fila_historial(run, i == idx)
        row.run_index = i
        listbox.append(row)

    if 0 <= idx < len(runs):
        listbox.select_row(listbox.get_row_at_index(idx))

    listbox.connect("row-selected", lambda lb, row: seleccionar_run(win, row))
    scroll.set_child(listbox)
    win.popover_hist.set_child(scroll)


def _crear_fila_historial(run, seleccionado):
    """Crea una fila del popover de historial."""
    row = Gtk.ListBoxRow()
    box = Gtk.Box(spacing=8, margin_start=10, margin_end=10, margin_top=5, margin_bottom=5)

    tstamp = run.get("timestamp", 0)
    kernel = run.get("kernel_version", "") or ""
    scxctl = run.get("scxctl_version", "") or ""
    run_id = run.get("id", 0)
    fecha = datetime.datetime.fromtimestamp(tstamp).strftime("%d/%m %H:%M")

    dot = Gtk.DrawingArea()
    dot.set_content_width(8)
    dot.set_content_height(8)
    dot.set_valign(Gtk.Align.CENTER)
    color = Gdk.RGBA()
    color.parse("#2ec27e" if seleccionado else "#7e7e7e")
    dot.set_draw_func(lambda a, cr, w, h, c=color: _pintar_dot(cr, w, h, c))
    box.append(dot)

    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    col.append(Gtk.Label(label=traducir("Run #{} — {}").format(run_id, fecha), xalign=0, css_classes=["heading"]))
    subtitulo = traducir("Kernel {}").format(kernel)
    if scxctl:
        subtitulo += traducir("  •  scxctl {}").format(scxctl)
    col.append(Gtk.Label(label=subtitulo, xalign=0, css_classes=["caption", "dim-label"]))
    box.append(col)

    row.set_child(box)
    return row


def _pintar_dot(cr, w, h, color):
    cr.set_source_rgba(color.red, color.green, color.blue, 0.9)
    cr.arc(w / 2, h / 2, 3.5, 0, 2 * 3.14159)
    cr.fill()


def _cargar_log_en_textview(win, run):
    buf = win.text_view_logs_auto.get_buffer()
    buf.set_text(run.get("log", ""))


def seleccionar_run(win, row):
    """Salta directamente al run seleccionado en el popover."""
    if row is None:
        return
    if getattr(win, "_cargando_historial", False):
        return
    idx = getattr(row, "run_index", -1)
    if idx < 0 or idx >= len(win.auto_state.historial_runs):
        return

    if idx == win.auto_state.indice_historial:
        return

    win.auto_state.cargando_historial = True
    win.auto_state.indice_historial = idx

    run = win.auto_state.historial_runs[idx]
    _cargar_log_en_textview(win, run)
    resultados = cargar_resultados_de_run(run["id"])
    brutos = reconstruir_brutos(resultados)

    if not brutos:
        win.toast_overlay.add_toast(Adw.Toast.new(traducir("Este run no contiene datos válidos.")))
        win.auto_state.cargando_historial = False
        return

    with win.auto_state.brutos_lock:
        win.auto_state.brutos_finales = brutos

    scores_finales = calcular_scores_finales(brutos)
    win.auto_state.ganador_final = max(scores_finales.keys(), key=lambda s: scores_finales[s]["score"])

    win.grafico.datos_raw = {}
    win.grafico.valores_animados = {}
    win.grafico.max_por_categoria = [1.0] * win.grafico.num_categorias

    while (c := win.box_leyenda.get_first_child()):
        win.box_leyenda.remove(c)

    for sc, sdata in brutos.items():
        win.grafico.registrar_scheduler(sc)
        crear_chip_leyenda(sc, win.grafico, win.box_leyenda)
        for tt, res in sdata.items():
            chart_idx = MAPA_CHART.get(tt)
            if chart_idx is not None:
                val_v = valor_para_grafico(res, tt)
                try:
                    win.grafico.actualizar_dato(sc, chart_idx, float(val_v) if not isinstance(val_v, (int, float)) else val_v)
                except (ValueError, TypeError):
                    continue

    win.grafico.queue_draw()

    win.fila_ganador.set_expanded(True)
    win.btn_auto.set_label(traducir("Determinar"))
    win.btn_auto.set_sensitive(True)
    win.btn_auto.add_css_class("suggested-action")
    win.btn_auto.remove_css_class("destructive-action")

    from ui.automatizacion.pesos import recalcular_ranking, poblar_ranking
    if win.auto_state.ajustando_pesos:
        recalcular_ranking(win)
    else:
        poblar_ranking(win)

    actualizar_popover(win)
    actualizar_botones_nav(win)

    win.toast_overlay.add_toast(Adw.Toast.new(
        traducir("Cargado: {}").format(run.get('kernel_version', ''))
    ))
    win.auto_state.cargando_historial = False


def actualizar_botones_nav(win):
    """Actualiza la etiqueta del botón de historial."""
    idx = win.auto_state.indice_historial
    total = len(win.auto_state.historial_runs)
    win.btn_hist.set_sensitive(total > 0)

    if 0 <= idx < total:
        ts = win.auto_state.historial_runs[idx].get("timestamp", 0)
        dt = datetime.datetime.fromtimestamp(ts)
        win.btn_hist.set_label(traducir("Run {}/{} — {}").format(idx + 1, total, dt.strftime('%d/%m %H:%M')))
    else:
        win.btn_hist.set_label(traducir("Sin historial"))


def navegar_historial(win, direccion):
    """Navega al run anterior (-1) o siguiente (+1) en el historial."""
    nuevo = win.auto_state.indice_historial + direccion
    if nuevo < 0 or nuevo >= len(win.auto_state.historial_runs):
        return

    win.auto_state.indice_historial = nuevo
    run = win.auto_state.historial_runs[nuevo]
    _cargar_log_en_textview(win, run)
    resultados = cargar_resultados_de_run(run["id"])
    brutos = reconstruir_brutos(resultados)

    if not brutos:
        win.toast_overlay.add_toast(Adw.Toast.new(traducir("Este run no contiene datos válidos.")))
        return

    with win.auto_state.brutos_lock:
        win.auto_state.brutos_finales = brutos

    scores_finales = calcular_scores_finales(brutos)
    win.auto_state.ganador_final = max(scores_finales.keys(), key=lambda s: scores_finales[s]["score"])

    win.grafico.datos_raw = {}
    win.grafico.valores_animados = {}
    win.grafico.max_por_categoria = [1.0] * win.grafico.num_categorias

    while (c := win.box_leyenda.get_first_child()):
        win.box_leyenda.remove(c)

    for sc, sdata in brutos.items():
        win.grafico.registrar_scheduler(sc)
        crear_chip_leyenda(sc, win.grafico, win.box_leyenda)

        for tt, res in sdata.items():
            chart_idx = MAPA_CHART.get(tt)
            if chart_idx is not None:
                val_v = valor_para_grafico(res, tt)
                win.grafico.actualizar_dato(sc, chart_idx, val_v)

    win.grafico.queue_draw()

    win.fila_ganador.set_expanded(True)
    win.btn_auto.set_label(traducir("Determinar"))
    win.btn_auto.set_sensitive(True)
    win.btn_auto.add_css_class("suggested-action")
    win.btn_auto.remove_css_class("destructive-action")

    from ui.automatizacion.pesos import recalcular_ranking, poblar_ranking
    if win.auto_state.ajustando_pesos:
        recalcular_ranking(win)
    else:
        poblar_ranking(win)

    actualizar_botones_nav(win)

    win.toast_overlay.add_toast(Adw.Toast.new(
        traducir("Cargado: {}").format(win.auto_state.historial_runs[nuevo].get('kernel_version', ''))
    ))
