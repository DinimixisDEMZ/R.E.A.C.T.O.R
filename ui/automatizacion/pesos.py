"""
Lógica de sliders de peso, presets y ranking dinámico.
"""

import random

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from utils.i18n import traducir
from core.scoring import calcular_scores_finales
from core.constantes import PESOS_POR_DEFECTO


def animar_sliders(win, d_pot, d_resp, d_flu, callback=None):
    pendientes = [3]

    def _hecho(*_a):
        pendientes[0] -= 1
        if pendientes[0] == 0 and callback:
            callback()

    for slider, destino_val in [
        (win.slider_pot, d_pot),
        (win.slider_resp, d_resp),
        (win.slider_flu, d_flu),
    ]:
        adj = slider.get_adjustment()
        destino = Adw.PropertyAnimationTarget.new(adj, "value")
        anim = Adw.TimedAnimation.new(
            slider, adj.get_value(), float(destino_val), 200, destino
        )
        anim.set_easing(Adw.Easing.EASE_OUT_QUAD)
        anim.connect("done", _hecho)
        anim.play()


def aplicar_preset(win, p_pot, p_resp, p_flu):
    win.auto_state.ajustando_pesos = True

    def despues():
        win._lbl_pot.set_label(f"{win.slider_pot.get_value():.0f}%")
        win._lbl_resp.set_label(f"{win.slider_resp.get_value():.0f}%")
        win._lbl_flu.set_label(f"{win.slider_flu.get_value():.0f}%")
        win.auto_state.ajustando_pesos = False
        if win.auto_state.brutos_finales:
            recalcular_ranking(win)

    animar_sliders(win, p_pot, p_resp, p_flu, callback=despues)


def sincronizar_estado_pesos(win, hay_datos):
    """Muestra/oculta filas de sliders y bloquea/habilita según hay_datos."""
    if not hasattr(win, '_row_pot'):
        return
    win._row_pot.set_visible(hay_datos)
    win._row_resp.set_visible(hay_datos)
    win._row_flu.set_visible(hay_datos)
    win._row_pot.set_sensitive(hay_datos)
    win._row_resp.set_sensitive(hay_datos)
    win._row_flu.set_sensitive(hay_datos)


def mostrar_banner_recalc(win):
    """Muestra la barra de progreso de recálculo con animación de pulse."""
    if not hasattr(win, 'revealer_recalc'):
        return
    win.revealer_recalc.set_reveal_child(True)
    if win.auto_state.recalc_timer:
        GLib.source_remove(win.auto_state.recalc_timer)
    win.auto_state.recalc_timer = GLib.timeout_add(50, lambda: win.barra_recalc.pulse() or True)

    def ocultar():
        if win.auto_state.recalc_timer:
            GLib.source_remove(win.auto_state.recalc_timer)
            win.auto_state.recalc_timer = 0
        win.barra_recalc.set_fraction(0.0)
        win.revealer_recalc.set_reveal_child(False)
        return False

    GLib.timeout_add(600, ocultar)


def recalcular_ranking(win):
    """Lee los sliders y repuebla el ranking."""
    mostrar_banner_recalc(win)
    pot = win.slider_pot.get_value()
    resp = win.slider_resp.get_value()
    flu = win.slider_flu.get_value()
    poblar_ranking(win, pesos=(pot, resp, flu))


def poblar_ranking(win, pesos=None):
    """Puebla la lista de ranking en fila_ganador usando _brutos_finales con pesos opcionales."""
    if not win.auto_state.brutos_finales:
        sincronizar_estado_pesos(win, False)
        return

    sincronizar_estado_pesos(win, True)

    for f in win._filas_ranking:
        win.fila_ganador.remove(f)
    win._filas_ranking.clear()

    if pesos:
        total = sum(pesos)
        if total > 0:
            pesos_norm = tuple(p / total for p in pesos)
        else:
            pesos_norm = PESOS_POR_DEFECTO
    else:
        pesos_norm = PESOS_POR_DEFECTO

    with win.auto_state.brutos_lock:
        brutos = win.auto_state.brutos_finales
        scores = calcular_scores_finales(brutos, pesos=pesos_norm)
        win.auto_state.scores_finales = scores

    ordenados = sorted(scores.items(), key=lambda x: x[1]['score'], reverse=True)

    for idx, (name, data) in enumerate(ordenados):
        row = Adw.ActionRow(title=name)
        row.set_subtitle(
            traducir("Potencia: {:.2f} | Respuesta: {:.2f} | Fluidez: {:.2f}").format(
                data['pot']/100, data['resp']/100, data['flu']/100
            )
        )

        talla_pct = max(70, 180 - (idx * 30))
        opacidad = max(0.4, 1.0 - (idx * 0.15))

        cls = ["bold"]
        if idx < 2:
            cls.append("accent")

        lbl_score = Gtk.Label(css_classes=cls)
        lbl_score.set_markup(f"<span size='{talla_pct}%'>{data['score']:.1f}%</span>")
        lbl_score.set_opacity(opacidad)
        row.add_suffix(lbl_score)

        if idx == 0:
            win.fila_ganador.set_expanded(True)
            row.add_css_class("success")

        win.fila_ganador.add_row(row)
        win._filas_ranking.append(row)

    ganador = ordenados[0][0] if ordenados else None
    if ganador:
        win.auto_state.ganador_final = ganador
        score_ganador = ordenados[0][1]['score']
        win.fila_ganador.set_title(traducir("Mejor Planificador: {}").format(ganador))
        win.fila_ganador.set_subtitle(
            traducir("'{}' ofrece la mejor propuesta integral con un {:.1f}% de eficacia de sistema.").format(ganador, score_ganador)
        )


def crear_slider_peso(nombre, icono, color_hex, default):
    """Crea una fila de slider de peso con icono, escala y etiqueta de valor."""
    row = Adw.PreferencesRow()
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_start=12, margin_end=12, margin_top=4, margin_bottom=4)
    img = Gtk.Image(icon_name=icono, pixel_size=14, css_classes=["dim-label"])
    adj = Gtk.Adjustment(value=default, lower=0, upper=100, step_increment=1, page_increment=10)
    scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj, hexpand=True, draw_value=False)
    lbl_val = Gtk.Label(label=f"{default:.0f}%", css_classes=["dim-label"], width_chars=4, xalign=1)
    box.append(img)
    box.append(scale)
    box.append(lbl_val)
    row.set_child(box)
    return row, scale, lbl_val


def on_peso_changed(win, slider):
    """Callback de cambio en cualquier slider de peso — redistribuye proporcionalmente."""
    if win.auto_state.ajustando_pesos:
        return

    pot = win.slider_pot.get_value()
    resp = win.slider_resp.get_value()
    flu = win.slider_flu.get_value()
    total = pot + resp + flu

    if total != 100 and total != 0:
        if slider is win.slider_pot:
            v1, v2 = resp, flu
            s1, s2 = win.slider_resp, win.slider_flu
        elif slider is win.slider_resp:
            v1, v2 = pot, flu
            s1, s2 = win.slider_pot, win.slider_flu
        else:
            v1, v2 = pot, resp
            s1, s2 = win.slider_pot, win.slider_resp

        remaining = 100 - slider.get_value()
        if v1 + v2 == 0 or remaining <= 0:
            n1 = n2 = round(remaining / 2) if remaining > 0 else 0
        else:
            n1 = round(remaining * v1 / (v1 + v2))
            n2 = remaining - n1

        win.auto_state.ajustando_pesos = True
        s1.set_value(n1)
        s2.set_value(n2)
        win.auto_state.ajustando_pesos = False

    actualizar_lbls(win)

    if win.auto_state.peso_timer > 0:
        GLib.source_remove(win.auto_state.peso_timer)
    win.auto_state.peso_timer = GLib.timeout_add(200, _finalizar_ajuste_pesos, win)


def actualizar_lbls(win):
    """Actualiza las etiquetas de porcentaje de cada slider."""
    win._lbl_pot.set_label(f"{win.slider_pot.get_value():.0f}%")
    win._lbl_resp.set_label(f"{win.slider_resp.get_value():.0f}%")
    win._lbl_flu.set_label(f"{win.slider_flu.get_value():.0f}%")


def _finalizar_ajuste_pesos(win):
    """Callback final tras debounce — normaliza a 100 y recalcula ranking."""
    win.auto_state.peso_timer = 0

    pot = win.slider_pot.get_value()
    resp = win.slider_resp.get_value()
    flu = win.slider_flu.get_value()
    total = pot + resp + flu

    if total == 0:
        aplicar_preset(win, 45, 45, 10)
        return False

    if total != 100:
        factor = 100.0 / total
        new_pot = round(pot * factor)
        new_resp = round(resp * factor)
        new_flu = round(flu * factor)

        win.auto_state.ajustando_pesos = True

        def despues():
            actualizar_lbls(win)
            win.auto_state.ajustando_pesos = False
            if win.auto_state.brutos_finales:
                recalcular_ranking(win)

        animar_sliders(win, new_pot, new_resp, new_flu, callback=despues)
        return False

    actualizar_lbls(win)
    if win.auto_state.brutos_finales:
        recalcular_ranking(win)

    return False


CHISTES_PESO = [
    traducir("Si ajustas los pesos y nada cambia, no es el planificador... eres tú."),
    traducir("45/45/10 es como pedir pizza: todos dicen que quieren lo mismo, pero nadie está conforme."),
    traducir("Un planificador justo no existe. Solo hay planificadores menos injustos."),
    traducir("Si pones todo en 33%, obtienes... un planificador que no sabe qué priorizar."),
    traducir("La fluidez no es lo mismo que ir rápido. Es no quedarse sin gasolina a mitad de carrera."),
    traducir("Fun fact: Linus Torvalds no ajusta sliders. Usa un stick."),
    traducir("¿Más potencia? Tu Ryzen 7 ya está dando todo. Respira."),
    traducir("Los pesos son como las reglas de la primera noche... siempre hay un traitor."),
    traducir("Si el planificador te pregunta por qué lo torturas, dile que es para su bien."),
    traducir("Dato curioso: el 99% de los ajustes de pesos son placebo. Pero el 1% restante... también."),
    traducir("Ajustar pesos es como arreglar un auto en marcha. Divertido hasta que algo explota."),
    traducir("Si te gusta el botón, dale otra vez. No tengo vida."),
    traducir("¿Otra vez? ¿Acaso esto es un benchmark de clicks?"),
    traducir("Contador oficial: ya llevas {n} clics en una bombillita. Impresionante."),
]


def on_info_click(win, btn):
    """Muestra un chiste aleatorio al hacer clic en el botón de info de pesos."""
    win.auto_state.info_clicks += 1
    idx = win.auto_state.info_clicks - 1
    if idx < 3:
        msg = CHISTES_PESO[idx]
    elif idx < len(CHISTES_PESO) - 1:
        msg = random.choice(CHISTES_PESO[3:-1])
    else:
        msg = CHISTES_PESO[-1].format(n=win.auto_state.info_clicks)
    win.toast_overlay.add_toast(Adw.Toast.new(msg))
