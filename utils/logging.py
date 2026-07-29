"""
Logging thread-safe para R.E.A.C.T.O.R.
Todas las funciones aceptan tv_log (Gtk.TextView) y logs (bool).
"""

from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Adw


def log(tv, texto, nivel="info"):
    GLib.idle_add(_append_log, tv, texto, nivel)


def _color_adwaita(tv, nombre):
    ctx = tv.get_style_context()
    found, rgba = ctx.lookup_color(nombre)
    if found:
        r = max(0, min(255, int(round(rgba.red * 255))))
        g = max(0, min(255, int(round(rgba.green * 255))))
        b = max(0, min(255, int(round(rgba.blue * 255))))
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _etiqueta_log(buf, nombre, color=None, negrita=False):
    tag_table = buf.get_tag_table()
    tag = tag_table.lookup(nombre)
    if tag is not None:
        return tag
    tag = buf.create_tag(nombre)
    if color:
        tag.set_property("foreground", color)
    if negrita:
        tag.set_property("weight", 700)
    return tag


def _append_log(tv, texto, nivel):
    from utils.helpers import limpiar_texto
    buf = tv.get_buffer()
    ts = datetime.now().strftime("%H:%M:%S")
    inicio_offset = buf.get_end_iter().get_offset()
    if nivel == "error":
        format_text = f"[{ts}] ERROR: {texto}\n"
        sufijo, negrita, color_prop = "error", True, "error_color"
    elif nivel == "warning":
        format_text = f"[{ts}] {texto}\n"
        sufijo, negrita, color_prop = "warn", False, "warning_color"
    elif nivel == "success":
        format_text = f"[{ts}] {texto}\n"
        sufijo, negrita, color_prop = "ok", True, "success_color"
    elif nivel == "title":
        format_text = f"\n{'─'*35}\n[{ts}] {texto}\n{'─'*35}\n"
        sufijo, negrita, color_prop = "title", True, None
    else:
        buf.insert(buf.get_end_iter(), f"[{ts}] {texto}\n")
        _scroll_log(tv)
        return
    buf.insert(buf.get_end_iter(), format_text)
    fin = buf.get_end_iter()
    color = _color_adwaita(tv, color_prop) if color_prop else None
    et = _etiqueta_log(buf, f"log-{sufijo}", color=color, negrita=negrita)
    buf.apply_tag(et, buf.get_iter_at_offset(inicio_offset), fin)
    _scroll_log(tv)


def _scroll_log(tv):
    buf = tv.get_buffer()
    adj = tv.get_vadjustment()
    if (adj.get_upper() - adj.get_value() - adj.get_page_size()) < 50:
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        tv.scroll_to_mark(mark, 0.0, True, 0.5, 1.0)
        buf.delete_mark(mark)


def log_subprocess_output(tv_log, logs, res, elapsed, label="Ejecutando", max_lines=50):
    if not logs or not tv_log:
        return
    from utils.helpers import limpiar_texto
    log(tv_log, f"{label}: {' '.join(res.args) if hasattr(res, 'args') else ''}")
    log(tv_log, f"Finalizado (exit={res.returncode}) en {elapsed:.2f}s")
    for stream_name, stream in [("STDOUT", res.stdout), ("STDERR", res.stderr)]:
        if not stream:
            continue
        for linea in limpiar_texto(stream).splitlines()[:max_lines]:
            keywords = ("warning", "error", "failed", "mean", "stddev")
            if any(p in linea.lower() for p in keywords):
                log(tv_log, f"{stream_name}: {linea}")


def mostrar_toast(win, mensaje, prefijo=""):
    msg = GLib.markup_escape_text(str(mensaje))
    texto = f"{prefijo}: {msg}" if prefijo else msg
    GLib.idle_add(lambda: win.toast_overlay.add_toast(Adw.Toast.new(texto)))


def log_error_benchmark(tv_log, logs, error, tool_name=""):
    if not logs or not tv_log:
        return
    if isinstance(error, FileNotFoundError):
        log(tv_log, f"Error: {tool_name or 'herramienta'} no encontrado. Instálalo en el sistema.", nivel="error")
    elif isinstance(error, __import__("subprocess").TimeoutExpired):
        log(tv_log, "Prueba excedió el tiempo límite.", nivel="error")
    else:
        log(tv_log, f"Error: {error}", nivel="error")
