"""
Pestaña de Disponibilidad: Verificación de compatibilidad BPF de schedulers.
"""

import subprocess
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from utils.helpers import log, limpiar_texto
from core.database import cargar_compatibilidad, guardar_compatibilidad, limpiar_compatibilidad, obtener_historial_compatibilidad
from utils.i18n import traducir


def _mensaje_corto(msg, compatible):
    if not msg:
        return traducir("Correcto") if compatible else traducir("Sin verificar")
    ml = msg.lower()
    if compatible:
        if "verificado" in ml: return traducir("Verificado")
        if "residente" in ml: return traducir("Residente")
        if "shutdown" in ml: return traducir("Apagado")
        if "simulado" in ml: return traducir("Simulado")
        return traducir("Correcto")
    if "failed to load bpf" in ml or "bpf" in ml: return traducir("Error BPF")
    if "no such file" in ml: return traducir("BPF faltante")
    if "error de salida" in ml:
        idx = msg.find("(")
        return traducir("Error {}").format(msg[idx:idx+6].strip()) if idx >= 0 else traducir("Error")
    if "error" in ml: return traducir("Error")
    return msg[:10] + "…" if len(msg) > 10 else msg


def _simplificar_kernel(kv):
    return kv.split("-")[0] if kv else kv


def _fade_in(widget, duration_ms=200):
    widget.set_opacity(0.0)
    destino = Adw.PropertyAnimationTarget.new(widget, "opacity")
    anim = Adw.TimedAnimation.new(widget, 0.0, 1.0, duration_ms, destino)
    anim.set_easing(Adw.Easing.EASE_OUT_QUAD)
    anim.play()


def configurar_ui_disponibilidad(win):
    """Construye la interfaz de la pestaña Disponibilidad."""
    win._disp_filas = {}
    win._verificando = False

    pref_page = Adw.PreferencesPage()
    win._disp_pref_page = pref_page

    grupo = Adw.PreferencesGroup(
        title=traducir("Compatibilidad de Planificadores"),
        description=traducir("Comprueba si el programa BPF de cada planificador puede cargarse en el kernel actual. La verificación requiere privilegios de administrador.")
    )

    try:
        nombres = win.scx.obtener_lista()
    except (subprocess.SubprocessError, OSError):
        nombres = []

    kernel_actual = win.versiones.get("kernel", "")
    cache = cargar_compatibilidad(kernel_actual) if kernel_actual else {}

    for nombre in nombres:
        row = Adw.ActionRow(title=nombre)

        suffix_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        spinner = Adw.Spinner()
        spinner.set_visible(False)
        icono = Gtk.Image.new_from_icon_name("dialog-question-symbolic")
        icono.add_css_class("dim-label")
        suffix_box.append(spinner)
        suffix_box.append(icono)
        row.add_suffix(suffix_box)

        if nombre in cache:
            compatible, mensaje, ts = cache[nombre]
            if compatible:
                icono.set_from_icon_name("emblem-ok-symbolic")
                icono.remove_css_class("dim-label")
                icono.add_css_class("success")
            else:
                icono.set_from_icon_name("dialog-error-symbolic")
                icono.remove_css_class("dim-label")
                icono.add_css_class("error")
            row.set_subtitle(_mensaje_corto(mensaje, compatible))
            if mensaje:
                row.set_tooltip_text(mensaje)
        else:
            row.set_subtitle(traducir("Sin verificar"))

        win._disp_filas[nombre] = (row, spinner, icono)
        grupo.add(row)

    win._disp_grupo_scheds = grupo
    pref_page.add(grupo)

    # ── Registro de Verificación ──
    win._disp_grupo_logs = Adw.PreferencesGroup(title=traducir("Registro de Verificación"))
    win.text_view_logs_disp = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["card"])
    win._dialog_logs_disp = None

    def _abrir_logs():
        if win._dialog_logs_disp is None:
            scrolled = Gtk.ScrolledWindow(min_content_height=400, vexpand=True)
            scrolled.set_child(win.text_view_logs_disp)
            win._dialog_logs_disp = Adw.Dialog()
            ancho = win.get_width()
            win._dialog_logs_disp.set_content_width(max(ancho - 40, 400))
            win._dialog_logs_disp.set_content_height(500)
            win._dialog_logs_disp.set_presentation_mode(Adw.DialogPresentationMode.BOTTOM_SHEET)
            win._dialog_logs_disp.set_child(scrolled)
        win._dialog_logs_disp.present(win)

    btn_logs = Adw.ActionRow(
        title=traducir("Terminal de Diagnóstico"),
        subtitle=traducir("Salida técnica de los binarios BPF"),
        icon_name="utilities-terminal-symbolic",
    )
    btn_logs.set_activatable(True)
    btn_logs.connect("activated", lambda *_: _abrir_logs())
    win._disp_grupo_logs.add(btn_logs)

    pref_page.add(win._disp_grupo_logs)

    # ── Historial de Compatibilidad por Kernel ──
    win._disp_grupo_historial = None
    _refrescar_historial_compat(win)

    header = Adw.HeaderBar()
    win._btn_verificar_disp = Gtk.Button(
        label=traducir("Comprobar"),
        css_classes=["suggested-action"],
        valign=Gtk.Align.CENTER
    )
    win._btn_verificar_disp.connect("clicked", lambda b: iniciar_verificacion(win, b))
    header.pack_start(win._btn_verificar_disp)

    btn_limpiar = Gtk.Button(
        icon_name="user-trash-symbolic",
        tooltip_text=traducir("Limpiar caché de compatibilidad"),
        css_classes=["flat"],
        valign=Gtk.Align.CENTER
    )
    btn_limpiar.connect("clicked", lambda b: _limpiar_cache(win))
    header.pack_end(btn_limpiar)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_disponibilidad.set_child(view)


def recargar_disponibilidad_ui(win):
    """Refresca la lista de schedulers en disponibilidad al cambiar modo simulación, sin destruir la página."""
    try:
        nuevos = win.scx.obtener_lista()
    except (subprocess.SubprocessError, OSError):
        nuevos = []

    kernel_actual = win.versiones.get("kernel", "")
    cache = cargar_compatibilidad(kernel_actual) if kernel_actual else {}

    antiguos = set(win._disp_filas.keys())
    nuevos_set = set(nuevos)

    grupo = win._disp_grupo_scheds

    # Eliminar filas que ya no corresponden
    for nombre in antiguos - nuevos_set:
        row, _, _ = win._disp_filas.pop(nombre)
        grupo.remove(row)

    # Añadir filas nuevas
    for nombre in nuevos_set - antiguos:
        row = Adw.ActionRow(title=nombre)

        suffix_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        spinner = Adw.Spinner()
        spinner.set_visible(False)
        icono = Gtk.Image.new_from_icon_name("dialog-question-symbolic")
        icono.add_css_class("dim-label")
        suffix_box.append(spinner)
        suffix_box.append(icono)
        row.add_suffix(suffix_box)

        if nombre in cache:
            compatible, mensaje, ts = cache[nombre]
            if compatible:
                icono.set_from_icon_name("emblem-ok-symbolic")
                icono.remove_css_class("dim-label")
                icono.add_css_class("success")
            else:
                icono.set_from_icon_name("dialog-error-symbolic")
                icono.remove_css_class("dim-label")
                icono.add_css_class("error")
            row.set_subtitle(_mensaje_corto(mensaje, compatible))
            if mensaje:
                row.set_tooltip_text(mensaje)
        else:
            row.set_subtitle(traducir("Sin verificar"))

        win._disp_filas[nombre] = (row, spinner, icono)
        grupo.add(row)

    _refrescar_historial_compat(win)


def _actualizar_fila(r, s, i, ok, texto, warn):
    """Actualiza el estado visual de una fila de scheduler."""
    s.set_visible(False)
    i.set_visible(True)
    r.set_subtitle(_mensaje_corto(texto, ok))
    r.set_tooltip_text(texto)
    for cls in ["success", "error", "dim-label", "warning"]:
        i.remove_css_class(cls)

    if ok:
        if warn:
            i.set_from_icon_name("dialog-warning-symbolic")
            i.add_css_class("warning")
        else:
            i.set_from_icon_name("emblem-ok-symbolic")
            i.add_css_class("success")
    else:
        i.set_from_icon_name("dialog-error-symbolic")
        i.add_css_class("error")


def _limpiar_cache(win):
    """Limpia la caché de compatibilidad y resetea las filas."""
    limpiar_compatibilidad()
    for nombre, (row, spinner, icono) in win._disp_filas.items():
        row.set_subtitle(traducir("Sin verificar"))
        row.set_tooltip_text("")
        icono.set_from_icon_name("dialog-question-symbolic")
        icono.remove_css_class("success")
        icono.remove_css_class("error")
        icono.remove_css_class("warning")
        icono.add_css_class("dim-label")
    _refrescar_historial_compat(win)
    log(win.text_view_logs_disp, traducir("Caché de compatibilidad limpiada"), nivel="title")


def _refrescar_historial_compat(win):
    """Refresca el historial de compatibilidad por kernel."""
    if win._disp_grupo_historial is not None:
        if win._disp_grupo_historial.get_parent():
            win._disp_pref_page.remove(win._disp_grupo_historial)
    if win._disp_grupo_logs is not None and win._disp_grupo_logs.get_parent():
        win._disp_pref_page.remove(win._disp_grupo_logs)

    win._disp_grupo_historial = Adw.PreferencesGroup(title=traducir("Historial de Compatibilidad"))
    win._disp_grupo_historial.add_css_class("history-group")

    datos = obtener_historial_compatibilidad()
    lookup = {}
    for d in datos:
        lookup[(d["scheduler_name"], d["kernel_version"])] = d

    scheds = sorted(win._disp_filas.keys())
    if not scheds:
        try:
            scheds = sorted(win.scx.obtener_lista())
        except (subprocess.SubprocessError, OSError):
            scheds = sorted(set(d["scheduler_name"] for d in datos))

    agregados = []
    for sched in scheds:
        sched_data = [d for d in datos if d["scheduler_name"] == sched]
        if not sched_data:
            continue
        total = len(sched_data)
        ok_count = sum(1 for d in sched_data if d["is_compatible"])

        if ok_count == total:
            sub = traducir("{}/{} verificados").format(total, total)
        elif ok_count == 0:
            sub = traducir("{}/{} fallidos").format(total, total)
        else:
            sub = traducir("{}/{} verificados").format(ok_count, total)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl_name = Gtk.Label(label=sched, xalign=0, css_classes=["heading"])
        box.append(lbl_name)
        lbl_sub = Gtk.Label(label=sub, xalign=0, css_classes=["caption", "dim-label"])
        _fade_in(lbl_sub, 300)
        box.append(lbl_sub)

        chips_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, margin_top=4)

        for kv in sorted(set(d["kernel_version"] for d in sched_data)):
            entry = lookup.get((sched, kv))
            if entry is None:
                continue

            compatible = entry["is_compatible"]
            msg = entry.get("message", "")
            korto = _mensaje_corto(msg, compatible)
            kv_brief = _simplificar_kernel(kv)

            if compatible:
                css_bg = "success"
                icon_name = "emblem-ok-symbolic"
            else:
                css_bg = "error"
                icon_name = "dialog-error-symbolic"

            chip = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
            chip.add_css_class("chip")
            chip.add_css_class(css_bg)
            chip.set_tooltip_text(f"{sched} @ {kv}\n{korto}: {msg}")

            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(14)
            chip.append(icon)
            chip.append(Gtk.Label(label=kv_brief))

            _fade_in(chip, 400)
            chips_box.append(chip)

        box.append(chips_box)
        win._disp_grupo_historial.add(box)
        agregados.append(sched)
        if len(agregados) > 0:
            sep = Gtk.Separator(margin_top=4, margin_bottom=4)
            sep.set_visible(True)
            win._disp_grupo_historial.add(sep)

    if not agregados:
        lbl = Gtk.Label(label=traducir("Esperando datos…"), css_classes=["dim-label", "caption"], margin_top=8, margin_bottom=8)
        _fade_in(lbl, 400)
        win._disp_grupo_historial.add(lbl)

    win._disp_pref_page.add(win._disp_grupo_logs)
    win._disp_pref_page.add(win._disp_grupo_historial)


def iniciar_verificacion(win, btn=None):
    """Inicia la verificación de compatibilidad BPF de todos los schedulers."""
    if win._verificando:
        return

    def _proceder():
        def _ejecutar():
            win._verificando = True
            lista_exitosos = []
            GLib.idle_add(win._btn_verificar_disp.set_sensitive, False)
            log(win.text_view_logs_disp, traducir("INICIANDO VERIFICACIÓN DE COMPATIBILIDAD BPF"), nivel="title")

            # Limpieza nuclear preventiva
            win.scx.detener_todos()
            time.sleep(1.5)

            for nombre, (row, spinner, icono) in list(win._disp_filas.items()):
                if win.modo_desarrollador:
                    time.sleep(0.1)
                    hash_val = hash(nombre) % 100
                    disponible = hash_val < 75
                    msg = traducir("Disponible (Simulado)") if disponible else traducir("Error: Programa incompatible (Simulado)")
                    is_warn = False
                    if disponible:
                        lista_exitosos.append(nombre)

                    kv_sim = win.versiones.get("kernel", "7.1.3-347.current")
                    guardar_compatibilidad(nombre, kv_sim, disponible, msg)
                    GLib.idle_add(lambda r=row, s=spinner, i=icono, ok=disponible, t=msg, w=is_warn:
                                  _actualizar_fila(r, s, i, ok, t, w))
                    continue

                def _reset(r=row, s=spinner, i=icono):
                    r.set_subtitle(traducir("Verificando..."))
                    i.set_visible(False)
                    s.set_visible(True)
                GLib.idle_add(_reset)

                disponible = False
                is_warn = False
                msg = traducir("Desconocido")

                try:
                    win.scx.detener_todos()
                    time.sleep(0.3)

                    log(win.text_view_logs_disp, traducir("Probando scx_{}...").format(nombre))
                    result = win.scx.ejecutar_con_sudo(["timeout", "-k", "1", "5", f"/usr/bin/scx_{nombre}"])
                    output = (result.stdout + result.stderr).strip()

                    if output:
                        output_limpio = limpiar_texto(output)
                        if output_limpio:
                            log(win.text_view_logs_disp, traducir("Resumen de scx_{}:\n{}").format(nombre, output_limpio))

                    has_error = any(kw in output for kw in [
                        "Failed to load BPF", "No such file or directory", "Error:"
                    ])

                    if has_error:
                        disponible = False
                        lineas = [l for l in output.splitlines() if l.strip() and "[INFO]" not in l]
                        msg = lineas[-1].strip() if lineas else traducir("Programa BPF incompatible")
                    else:
                        log_success_keywords = ["Calibration complete", "scheduler started", "Received shutdown signal", "ACTIVE"]
                        started_ok = any(kw in output for kw in log_success_keywords)

                        if result.returncode in [0, 124, 137] or started_ok:
                            disponible = True
                            is_warn = (result.returncode == 0 and not started_ok)

                            if started_ok:
                                msg = traducir("Disponible (Verificado)")
                            elif result.returncode in [124, 137]:
                                msg = traducir("Disponible (Residente)")
                            else:
                                msg = traducir("Disponible (Shutdown detectado)")

                            lista_exitosos.append(nombre)
                        else:
                            disponible = False
                            lineas = [l for l in output.splitlines() if l.strip()]
                            msg = lineas[-1].strip() if lineas else traducir("Error de salida ({})").format(result.returncode)

                except (subprocess.SubprocessError, OSError) as e:
                    disponible = False
                    msg = str(e)

                msg_safe = GLib.markup_escape_text(msg)

                guardar_compatibilidad(nombre, win.versiones.get("kernel", ""), disponible, msg_safe)
                GLib.idle_add(lambda r=row, s=spinner, i=icono, ok=disponible, t=msg_safe, w=is_warn:
                              _actualizar_fila(r, s, i, ok, t, w))
                win.scx.ejecutar_con_sudo(["scxctl", "stop"])

            log(win.text_view_logs_disp, traducir("VERIFICACIÓN FINALIZADA"), nivel="title")
            win.compatibles = lista_exitosos
            GLib.idle_add(lambda: _refrescar_historial_compat(win))
            try:
                from ui.automatizacion import _refrescar_auto_schedulers
                GLib.idle_add(lambda: _refrescar_auto_schedulers(win))
            except ImportError:
                pass

            def _update_badge():
                win.nav_disponibilidad.remove_css_class("pulse-warning")
                img = win.nav_disponibilidad.get_child().get_first_child()
                if isinstance(img, Gtk.Image):
                    for cls in ["success", "error"]:
                        img.remove_css_class(cls)

                    if lista_exitosos:
                        img.set_from_icon_name("emblem-ok-symbolic")
                        img.add_css_class("success")
                    else:
                        img.set_from_icon_name("dialog-error-symbolic")
                        img.add_css_class("error")
            GLib.idle_add(_update_badge)

            GLib.idle_add(win.sincronizar_sistema)
            GLib.idle_add(win._btn_verificar_disp.set_sensitive, nivel="title")
            win._verificando = False

        threading.Thread(target=_ejecutar, daemon=True).start()

    win.solicitar_sudo_si_necesario(_proceder)
