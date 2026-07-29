"""
Funciones de utilidad general.
"""

import os
import re
import time

RE_RUNNING = re.compile(r"running\s+([\w\.-]+)(?:.*(?:in\s+|\[)([\w-]+)(?:\]|\s+mode)?)?", re.IGNORECASE)
RE_JSON_ARRAY = re.compile(r'\[[\s\S]*\]')
RE_ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def limpiar_texto(texto):
    if not texto:
        return ""
    limpio = RE_ANSI.sub('', texto)
    lineas = limpio.splitlines()
    filtradas = [l for l in lineas if not any(c in l for c in "│─┐└┘┌├┤┼█▓▒░")]
    muy_filtradas = [l for l in filtradas if len(l.strip()) > 2]
    return "\n".join(muy_filtradas).strip()


def ruta_bundleada(subpath: str) -> str | None:
    appdir = os.environ.get("APPDIR")
    if not appdir:
        return None
    ruta = os.path.join(appdir, subpath)
    return ruta if os.path.exists(ruta) else None


def format_raw_value(val):
    if val is None or val == 0:
        return "0"
    if val >= 1000000:
        return f"{val/1000000:.2f}M"
    if val >= 1000:
        return f"{val:,.0f}".replace(",", ".")
    if isinstance(val, float):
        return f"{val:.1f}".rstrip("0").rstrip(".")
    return str(val)


def vaciar_contenedor(container):
    while (c := container.get_first_child()):
        container.remove(c)


def resultado_base(scx_manager, tipo, valor, p95, waste, sc_act=None, modo_act=None):
    from core.constantes import SISTEMA_BASE
    if sc_act is None or modo_act is None:
        sc_act, modo_act = scx_manager.obtener_estado()
    sc_act = sc_act or SISTEMA_BASE
    return {
        "tipo": tipo, "valor": valor, "p95": p95, "waste": waste,
        "sched": sc_act if sc_act != SISTEMA_BASE else "scx_rusty",
        "modo": modo_act or "default",
        "timestamp": time.time()
    }
