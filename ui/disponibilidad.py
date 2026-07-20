"""
Pestaña de Disponibilidad: Verificación de compatibilidad BPF de schedulers.
"""

import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from utils.helpers import log, limpiar_texto
from core.database import cargar_compatibilidad, guardar_compatibilidad, limpiar_compatibilidad


def setup_disponibilidad_ui(win):
    """Construye la interfaz de la pestaña Disponibilidad.
    
    Args:
        win: Instancia de VentanaSimple
    """
    win._disp_filas = {}
    win._verificando = False

    pref_page = Adw.PreferencesPage()

    grupo = Adw.PreferencesGroup(
        title="Compatibilidad de Planificadores",
        description="Comprueba si el programa BPF de cada planificador puede cargarse en el kernel actual. La verificación requiere privilegios de administrador."
    )

    try:
        rl = win.scx.scx_run(["scxctl", "list"])
        from utils.helpers import RE_JSON_ARRAY
        import json
        match_json = RE_JSON_ARRAY.search(rl.stdout)
        nombres = json.loads(match_json.group()) if match_json else []
    except Exception:
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
            row.set_subtitle(mensaje or ("Disponible" if compatible else "No disponible"))
        else:
            row.set_subtitle("Sin verificar")

        win._disp_filas[nombre] = (row, spinner, icono)
        grupo.add(row)

    pref_page.add(grupo)

    grupo_logs_disp = Adw.PreferencesGroup(title="Registro de Verificación")
    win.expander_logs_disp = Adw.ExpanderRow(title="Terminal de Diagnóstico", subtitle="Salida técnica de los binarios BPF", icon_name="utilities-terminal-symbolic")

    win.text_view_logs_disp = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["card"])
    caja_log_disp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
    scrolled_disp = Gtk.ScrolledWindow(min_content_height=200, vexpand=True)
    scrolled_disp.set_child(win.text_view_logs_disp)
    caja_log_disp.append(scrolled_disp)
    win.expander_logs_disp.add_row(caja_log_disp)
    grupo_logs_disp.add(win.expander_logs_disp)

    pref_page.add(grupo_logs_disp)

    header = Adw.HeaderBar()
    win._btn_verificar_disp = Gtk.Button(
        label="Comprobar",
        css_classes=["suggested-action"],
        valign=Gtk.Align.CENTER
    )
    win._btn_verificar_disp.connect("clicked", lambda b: iniciar_verificacion(win, b))
    header.pack_start(win._btn_verificar_disp)

    btn_limpiar = Gtk.Button(
        icon_name="user-trash-symbolic",
        tooltip_text="Limpiar caché de compatibilidad",
        css_classes=["flat"],
        valign=Gtk.Align.CENTER
    )
    btn_limpiar.connect("clicked", lambda b: _limpiar_cache(win))
    header.pack_end(btn_limpiar)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_disponibilidad.set_child(view)


def _actualizar_fila(r, s, i, ok, texto, warn):
    """Actualiza el estado visual de una fila de scheduler."""
    s.set_visible(False)
    i.set_visible(True)
    r.set_subtitle(texto)
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
        row.set_subtitle("Sin verificar")
        icono.set_from_icon_name("dialog-question-symbolic")
        icono.remove_css_class("success")
        icono.remove_css_class("error")
        icono.remove_css_class("warning")
        icono.add_css_class("dim-label")
    log(win.text_view_logs_disp, "Caché de compatibilidad limpiada", True)


def iniciar_verificacion(win, btn=None):
    """Inicia la verificación de compatibilidad BPF de todos los schedulers."""
    if win._verificando:
        return

    def _proceder():
        def _ejecutar():
            win._verificando = True
            lista_exitosos = []
            GLib.idle_add(win._btn_verificar_disp.set_sensitive, False)
            log(win.text_view_logs_disp, "INICIANDO VERIFICACIÓN DE COMPATIBILIDAD BPF", True)

            # Limpieza nuclear preventiva
            win.scx.ejecutar_con_sudo(["scxctl", "stop"])
            win.scx.ejecutar_con_sudo(["pkill", "-9", "-f", "scx_"])
            time.sleep(1.5)

            for nombre, (row, spinner, icono) in list(win._disp_filas.items()):
                if win.modo_desarrollador:
                    import random
                    time.sleep(0.1)
                    disponible = random.choice([True, True, True, False])
                    msg = "Disponible (Simulado)" if disponible else "Error: Programa incompatible (Simulado)"
                    is_warn = False
                    if disponible:
                        lista_exitosos.append(nombre)

                    guardar_compatibilidad(nombre, win.versiones.get("kernel", ""), disponible, msg)
                    GLib.idle_add(lambda r=row, s=spinner, i=icono, ok=disponible, t=msg, w=is_warn:
                                  _actualizar_fila(r, s, i, ok, t, w))
                    continue

                def _reset(r=row, s=spinner, i=icono):
                    r.set_subtitle("Verificando...")
                    i.set_visible(False)
                    s.set_visible(True)
                GLib.idle_add(_reset)

                disponible = False
                is_warn = False
                msg = "Desconocido"

                try:
                    win.scx.ejecutar_con_sudo(["scxctl", "stop"])
                    win.scx.ejecutar_con_sudo(["pkill", "-9", "-f", "scx_"])
                    time.sleep(0.3)

                    log(win.text_view_logs_disp, f"Probando scx_{nombre}...")
                    result = win.scx.ejecutar_con_sudo(["timeout", "-k", "1", "5", f"/usr/bin/scx_{nombre}"])
                    output = (result.stdout + result.stderr).strip()

                    if output:
                        output_limpio = limpiar_texto(output)
                        if output_limpio:
                            log(win.text_view_logs_disp, f"Resumen de scx_{nombre}:\n{output_limpio}")

                    has_error = any(kw in output for kw in [
                        "Failed to load BPF", "No such file or directory", "Error:"
                    ])

                    if has_error:
                        disponible = False
                        lineas = [l for l in output.splitlines() if l.strip() and "[INFO]" not in l]
                        msg = lineas[-1].strip() if lineas else "Programa BPF incompatible"
                    else:
                        log_success_keywords = ["Calibration complete", "scheduler started", "Received shutdown signal", "ACTIVE"]
                        started_ok = any(kw in output for kw in log_success_keywords)

                        if result.returncode in [0, 124, 137] or started_ok:
                            disponible = True
                            is_warn = (result.returncode == 0 and not started_ok)

                            if started_ok:
                                msg = "Disponible (Verificado)"
                            elif result.returncode in [124, 137]:
                                msg = "Disponible (Residente)"
                            else:
                                msg = "Disponible (Shutdown detectado)"

                            lista_exitosos.append(nombre)
                        else:
                            disponible = False
                            lineas = [l for l in output.splitlines() if l.strip()]
                            msg = lineas[-1].strip() if lineas else f"Error de salida ({result.returncode})"

                except Exception as e:
                    disponible = False
                    msg = str(e)

                msg_safe = GLib.markup_escape_text(msg)

                guardar_compatibilidad(nombre, win.versiones.get("kernel", ""), disponible, msg_safe)
                GLib.idle_add(lambda r=row, s=spinner, i=icono, ok=disponible, t=msg_safe, w=is_warn:
                              _actualizar_fila(r, s, i, ok, t, w))
                win.scx.ejecutar_con_sudo(["scxctl", "stop"])

            log(win.text_view_logs_disp, "VERIFICACIÓN FINALIZADA", True)
            win.compatibles = lista_exitosos

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
            GLib.idle_add(win._btn_verificar_disp.set_sensitive, True)
            win._verificando = False

        threading.Thread(target=_ejecutar, daemon=True).start()

    win.solicitar_sudo_si_necesario(_proceder)
