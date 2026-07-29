"""
Diálogo de verificación del sistema.
Muestra los resultados con Adw.ExpanderRow + spinner + iconos tintados.
"""

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

from core.verificacion import ejecutar_verificaciones, todo_critico_ok
from utils.i18n import traducir
from utils.iconos import OK, ERROR, ADVERTENCIA, PREGUNTA


def mostrar_verificacion(win, automatico=False):
    dialog = Adw.Dialog()
    dialog.set_content_width(580)
    dialog.set_content_height(650)
    dialog.set_presentation_mode(Adw.DialogPresentationMode.BOTTOM_SHEET)
    dialog.set_title(traducir("Verificación del Sistema"))

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    scrolled = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
    scrolled.set_child(box)
    scrolled.set_propagate_natural_width(True)

    # ── Header ──
    header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                         margin_start=20, margin_end=20, margin_top=20, margin_bottom=8)
    titulo = Gtk.Label(label=traducir("Verificación del Sistema"), css_classes=["title-1"], xalign=0)
    header_box.append(titulo)
    subtitulo = Gtk.Label(
        label=traducir("Comprobando herramientas, comandos y dependencias…"),
        css_classes=["body", "dim-label"], xalign=0, wrap=True
    )
    header_box.append(subtitulo)
    box.append(header_box)

    # ── Barra de progreso ──
    barra = Gtk.ProgressBar(margin_start=20, margin_end=20, margin_bottom=8, show_text=False)
    box.append(barra)

    # ── Grupo de checks ──
    grupo = Adw.PreferencesGroup(margin_start=12, margin_end=12)
    box.append(grupo)

    expanders: list[Adw.ExpanderRow] = []
    spinners: list[Adw.Spinner] = []
    iconos: list[Gtk.Image] = []
    filas_detalle: list[Adw.ActionRow] = []

    from core.verificacion import VERIFICACIONES
    for v in VERIFICACIONES:
        exp = Adw.ExpanderRow(title=v.nombre)

        suffix_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        spinner = Adw.Spinner()
        spinner.set_visible(True)
        icono = Gtk.Image.new_from_icon_name(PREGUNTA)
        icono.add_css_class("dim-label")
        icono.set_visible(False)
        suffix_box.append(spinner)
        suffix_box.append(icono)
        exp.add_suffix(suffix_box)

        det = Adw.ActionRow(
            title="",
            css_classes=["caption", "dim-label"],
        )
        det.set_visible(False)
        exp.add_row(det)

        grupo.add(exp)
        expanders.append(exp)
        spinners.append(spinner)
        iconos.append(icono)
        filas_detalle.append(det)

    # ── Botones ──
    btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      halign=Gtk.Align.CENTER, margin_start=20, margin_end=20, margin_top=16, margin_bottom=20)
    btn_reintentar = Gtk.Button(label=traducir("Reintentar"), css_classes=["pill"])
    btn_continuar = Gtk.Button(label=traducir("Continuar"), css_classes=["pill", "suggested-action"])
    btn_continuar.set_sensitive(False)
    btn_box.append(btn_reintentar)
    btn_box.append(btn_continuar)
    box.append(btn_box)

    # ── Actualización visual ──
    def _actualizar(i, v, r):
        spinners[i].set_visible(False)
        iconos[i].set_visible(True)
        for cls in ["success", "error", "warning", "dim-label"]:
            iconos[i].remove_css_class(cls)

        if r.exito:
            iconos[i].set_from_icon_name(OK)
            iconos[i].add_css_class("success")
            expanders[i].set_subtitle(r.mensaje[:80])
        elif not v.critico:
            iconos[i].set_from_icon_name(ADVERTENCIA)
            iconos[i].add_css_class("warning")
            expanders[i].set_subtitle(r.mensaje[:80])
            if r.sugerencia or r.detalles:
                filas_detalle[i].set_title(r.sugerencia or r.detalles or r.mensaje)
                filas_detalle[i].set_visible(True)
        else:
            iconos[i].set_from_icon_name(ERROR)
            iconos[i].add_css_class("error")
            expanders[i].set_subtitle(r.mensaje[:80])
            if r.sugerencia or r.detalles:
                filas_detalle[i].set_title(r.sugerencia or r.detalles or r.mensaje)
                filas_detalle[i].set_visible(True)

    def _completado(resultados):
        total = len(resultados)
        ok = sum(1 for _, r in resultados if r.exito)
        barra.set_fraction(1.0)
        barra.set_show_text(True)
        barra.set_text(f"{ok}/{total}")

        for i, (v, r) in enumerate(resultados):
            _actualizar(i, v, r)

        criticos_ok = todo_critico_ok(resultados)
        btn_continuar.set_sensitive(criticos_ok)
        if criticos_ok:
            subtitulo.set_label(traducir("Todos los chequeos críticos pasaron. Podés usar R.E.A.C.T.O.R."))
        else:
            subtitulo.set_label(traducir("Hay fallos críticos. Revisá los detalles y reinstalá lo necesario."))

    def _ejecutar():
        resultados = ejecutar_verificaciones()
        GLib.idle_add(lambda: _completado(resultados))

    barra_source = [0]

    def _animar_barra():
        val = barra.get_fraction()
        if val < 0.95:
            barra.set_fraction(val + 0.05)
            return True
        return False

    barra_source[0] = GLib.timeout_add(80, _animar_barra)

    def _reiniciar():
        for s in spinners:
            s.set_visible(True)
        for ic in iconos:
            ic.set_visible(False)
            for cls in ["success", "error", "warning", "dim-label"]:
                ic.remove_css_class(cls)
        for d in filas_detalle:
            d.set_visible(False)
        for e in expanders:
            e.set_subtitle("")
            e.set_expanded(False)
        btn_continuar.set_sensitive(False)
        subtitulo.set_label(traducir("Comprobando herramientas, comandos y dependencias…"))
        barra.set_fraction(0.0)
        barra.set_show_text(False)
        barra_source[0] = GLib.timeout_add(80, _animar_barra)
        threading.Thread(target=_ejecutar, daemon=True).start()

    def _limpiar_timer():
        if barra_source[0]:
            GLib.source_remove(barra_source[0])
            barra_source[0] = 0

    btn_reintentar.connect("clicked", lambda b: _reiniciar())
    btn_continuar.connect("clicked", lambda b: dialog.close())
    dialog.connect("closed", lambda *_: _limpiar_timer())

    # ── Cerrar con Escape ──
    key_controller = Gtk.EventControllerKey()
    def _on_key(c, k, m, d):
        if k == Gdk.KEY_Escape:
            dialog.close()
            return True
        return False
    key_controller.connect("key-pressed", _on_key)

    dialog.set_child(scrolled)
    dialog.present(win)

    threading.Thread(target=_ejecutar, daemon=True).start()
    return dialog
