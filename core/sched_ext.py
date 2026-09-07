"""
Observación del subsistema sched_ext del kernel.

Todo lo que hay acá se lee de `/sys/kernel/sched_ext/` **sin privilegios y sin
`scxctl`**. Es la fuente de verdad sobre qué está gobernando el sistema: el
loader puede no estar instalado, puede haber muerto, o el usuario puede haber
arrancado un scheduler a mano desde una terminal. El kernel siempre sabe.

Detalles que importan y que no son obvios:

  - `state` sólo puede valer `enabling`, `enabled`, `disabling` o `disabled`.
    No es un booleano: tratar los estados de transición como "activo" o
    "inactivo" produce parpadeos en la interfaz justo después de aplicar un
    cambio.

  - `root/ops` publica el nombre del scheduler **sin** el prefijo `scx_`
    (`bpfland`, no `scx_bpfland`), mientras que el loader y `scxctl` lo usan
    **con** prefijo. Comparar ambos sin normalizar hace que la interfaz muestre
    "ninguno activo" con un scheduler corriendo.

  - El directorio `root/` se crea *antes* de que `state` pase a `enabling`, así
    que su mera existencia no prueba que haya un scheduler gobernando. La
    autoridad es `state`; `root/ops` sólo aporta el nombre.
"""

import glob
import os

from core.hardware import leer_entero, leer_texto

RUTA_SCHED_EXT = "/sys/kernel/sched_ext"

ESTADOS_ACTIVOS = ("enabled",)
ESTADOS_TRANSITORIOS = ("enabling", "disabling")


def hay_soporte():
    """True si el kernel se compiló con CONFIG_SCHED_CLASS_EXT=y.

    Es el único requisito de kernel real de la aplicación.
    """
    return os.path.isdir(RUTA_SCHED_EXT)


def canonizar(nombre):
    """Normaliza un nombre de scheduler al formato con prefijo (`scx_lavd`).

    El kernel lo publica sin prefijo y el loader con prefijo; toda la aplicación
    trabaja con la forma canónica para que las comparaciones funcionen.
    """
    if not nombre:
        return None
    nombre = nombre.strip()
    if not nombre:
        return None
    return nombre if nombre.startswith("scx_") else f"scx_{nombre}"


def nombre_corto(nombre):
    """Quita el prefijo `scx_` para mostrar el nombre en la interfaz."""
    return nombre.removeprefix("scx_") if nombre else None


def estado():
    """Estado del planificador BPF según el kernel.

    Returns:
        dict con:
          soportado: el kernel tiene sched_ext.
          estado: 'enabled' | 'enabling' | 'disabling' | 'disabled' | 'desconocido'.
          activo: hay un scheduler BPF gobernando ahora mismo.
          en_transicion: está cargándose o descargándose.
          scheduler: nombre canónico del scheduler activo, o None.
          parcial: True si sólo gobierna las tareas SCHED_EXT (modo parcial).
    """
    if not hay_soporte():
        return {"soportado": False, "estado": "desconocido", "activo": False,
                "en_transicion": False, "scheduler": None, "parcial": False}

    crudo = (leer_texto(f"{RUTA_SCHED_EXT}/state") or "desconocido").lower()
    activo = crudo in ESTADOS_ACTIVOS
    conmutacion_total = leer_entero(f"{RUTA_SCHED_EXT}/switch_all", 0)

    scheduler = None
    if activo or crudo in ESTADOS_TRANSITORIOS:
        # El subdirectorio se llama "root" en los kernels actuales, pero se
        # busca con glob para no depender del nombre.
        for ruta in sorted(glob.glob(f"{RUTA_SCHED_EXT}/*/ops")):
            scheduler = canonizar(leer_texto(ruta))
            if scheduler:
                break

    return {
        "soportado": True,
        "estado": crudo,
        "activo": activo,
        "en_transicion": crudo in ESTADOS_TRANSITORIOS,
        "scheduler": scheduler,
        "parcial": activo and not conmutacion_total,
    }


def contadores():
    """Contadores globales de sched_ext, útiles para el panel de diagnóstico."""
    return {
        "rechazadas": leer_entero(f"{RUTA_SCHED_EXT}/nr_rejected", 0),
        "secuencia_activacion": leer_entero(f"{RUTA_SCHED_EXT}/enable_seq", 0),
        "secuencia_hotplug": leer_entero(f"{RUTA_SCHED_EXT}/hotplug_seq", 0),
        "conmutacion_total": bool(leer_entero(f"{RUTA_SCHED_EXT}/switch_all", 0)),
    }


def eventos():
    """Contadores por evento del scheduler activo (`root/events`).

    Existe desde el kernel 7.0 y sólo mientras hay un scheduler cargado.
    Devuelve un diccionario vacío si no está disponible.

    Los valores son acumulados desde que se cargó el scheduler: para mostrarlos
    en vivo conviene presentar la diferencia entre lecturas, no el absoluto.
    """
    resultado = {}
    for ruta in sorted(glob.glob(f"{RUTA_SCHED_EXT}/*/events")):
        contenido = leer_texto(ruta)
        if not contenido:
            continue
        for linea in contenido.splitlines():
            if not linea.strip():
                continue
            # Formato "NOMBRE valor" o "NOMBRE : valor".
            partes = linea.replace(":", " ").split()
            if len(partes) < 2:
                continue
            try:
                resultado[partes[0]] = int(partes[-1])
            except ValueError:
                continue
        if resultado:
            break
    return resultado


def schedulers_instalados():
    """Schedulers scx encontrados en el PATH del sistema.

    Se busca en el PATH y no en `/usr/bin` fijo: hay distribuciones que los
    instalan en `/usr/local/bin`, y en Nix o en un AppImage la ruta es otra.

    Returns:
        dict {nombre_canonico: ruta_absoluta}, ordenado por nombre.
    """
    encontrados = {}
    directorios = os.environ.get("PATH", "/usr/bin:/usr/local/bin").split(os.pathsep)
    # Rutas habituales que a veces no están en el PATH de una sesión gráfica.
    directorios += ["/usr/bin", "/usr/local/bin", "/usr/sbin", "/opt/scx/bin"]
    for directorio in directorios:
        if not directorio or not os.path.isdir(directorio):
            continue
        try:
            for entrada in os.listdir(directorio):
                if not entrada.startswith("scx_"):
                    continue
                ruta = os.path.join(directorio, entrada)
                if entrada in encontrados:
                    continue
                if os.path.isfile(ruta) and os.access(ruta, os.X_OK):
                    encontrados[entrada] = ruta
        except OSError:
            continue
    return dict(sorted(encontrados.items()))


def tarea_en_sched_ext(pid=None):
    """True si la tarea indicada está siendo planificada por sched_ext.

    Lee `ext.enabled` de `/proc/<pid>/sched`. Sirve para confirmar que un
    scheduler recién aplicado realmente se apoderó de las tareas, en vez de
    asumirlo tras una espera fija.
    """
    objetivo = "self" if pid is None else str(pid)
    contenido = leer_texto(f"/proc/{objetivo}/sched")
    if not contenido:
        return False
    for linea in contenido.splitlines():
        if "ext.enabled" in linea:
            partes = linea.replace(":", " ").split()
            try:
                return int(partes[-1]) == 1
            except (ValueError, IndexError):
                return False
    return False
