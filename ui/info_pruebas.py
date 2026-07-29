"""
Contenido informativo de cada prueba de rendimiento.
Fuente única para los bottomsheets de ayuda en Rendimiento.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from utils.i18n import traducir

INFO_PRUEBAS = {
    "cpu": {
        "titulo": traducir("Cambio de Contexto"),
        "explicacion": traducir(
            "Mide la capacidad del scheduler para cambiar entre tareas "
            "que compiten por la CPU. stress-ng ejecuta múltiples hilos "
            "que alternan entre cálculos intensivos, forzando al kernel "
            "a conmutar contexto constantemente."
        ),
        "estudio": traducir(
            "Un valor bajo (pts) indica mejor rendimiento: el scheduler "
            "completa más cambios de contexto por segundo. Compará "
            "planificadores ejecutando esta prueba con distintos schedulers activos."
        ),
        "detalle": traducir(
            "El stressor 'cpu' de stress-ng ejecuta operaciones aritméticas "
            "en hilos concurrentes. La métrica principal es la tasa de "
            "context switches reales del kernel, no operaciones de usuario. "
            "Relevante para cargas de trabajo con muchos procesos breves "
            "(compilación, servidores web, scripting)."
        ),
    },
    "threads": {
        "titulo": traducir("Carga Mixta"),
        "explicacion": traducir(
            "Simula un escritorio real con múltiples aplicaciones activas: "
            "hilos en segundo plano, operaciones de E/S simuladas y "
            "cambios entre tareas de diferente prioridad. stress-ng "
            "combina stressors de thread, io-uring y sleep."
        ),
        "estudio": traducir(
            "Un valor alto (ops/s) es mejor. Indica que el scheduler "
            "mantiene la fluidez del sistema aunque haya múltiples "
            "procesos de distinta naturaleza compitiendo por recursos."
        ),
        "detalle": traducir(
            "Esta prueba combina varios stressors de stress-ng para "
            "emular la heterogeneidad de un escritorio real: hilos "
            "bloqueantes, cómputo en foreground y ráfagas de E/S. "
            "Un buen scheduler aquí se traduce en un sistema que no "
            "se siente lento bajo carga mixta."
        ),
    },
    "memory": {
        "titulo": traducir("Sincronización"),
        "explicacion": traducir(
            "Evalúa la eficiencia del scheduler en operaciones de "
            "sincronización entre hilos. stress-ng dispara múltiples "
            "hilos que compiten por cerrojos mutex, midiendo el tiempo "
            "de adquisición y liberación."
        ),
        "estudio": traducir(
            "Un valor bajo (µs) es mejor. Menor latencia en mutex "
            "implica que el scheduler despierta y duerme hilos de "
            "forma eficiente, reduciendo contención."
        ),
        "detalle": traducir(
            "La métrica 'nanosecs-per-mutex' se obtiene del stressor "
            "--mutex de stress-ng. Cientos de hilos compiten por un "
            "conjunto limitado de mutex, forzando al scheduler a "
            "decidir qué hilo ejecutar y cuál bloquear. Un scheduler "
            "con buena sincronización reduce el sobrecoste de "
            "contención, mejorando el rendimiento en bases de datos, "
            "servidores y aplicaciones concurrentes."
        ),
    },
    "fork": {
        "titulo": traducir("Creación de Procesos"),
        "explicacion": traducir(
            "Mide la latencia de crear y ejecutar un proceso hijo "
            "(fork+exec). hyperfine ejecuta un comando corto repetidas "
            "veces y calcula el tiempo medio, percentiles y desviación."
        ),
        "estudio": traducir(
            "Un valor bajo (µs) es mejor. Menor latencia en fork+exec "
            "indica que el scheduler maneja eficientemente la creación "
            "de nuevos procesos, crucial para shells, scripts y "
            "servidores que lanzan procesos frecuentemente."
        ),
        "detalle": traducir(
            "hyperfine ejecuta 'sh -c \"true\"' para medir el tiempo "
            "de fork+exec en vacío, y 'sh -c \"exit\"' para medir "
            "solo el exec. La diferencia entre ambas da el costo "
            "neto del fork. Se realizan múltiples iteraciones con "
            "warmup para estabilizar la medición."
        ),
    },
    "compile": {
        "titulo": traducir("Compilación Paralela"),
        "explicacion": traducir(
            "Mide el rendimiento de compilación paralela usando make -j. "
            "hyperfine cronometra una compilación real con todos los "
            "núcleos disponibles, evaluando qué tan bien el scheduler "
            "distribuye y balancea hilos CPU-intensivos."
        ),
        "estudio": traducir(
            "Un valor bajo (µs) es mejor. Menor tiempo de compilación "
            "implica que el scheduler reparte eficazmente la carga "
            "entre núcleos sin dejar CPUs ociosas ni generar "
            "contención excesiva."
        ),
        "detalle": traducir(
            "Se compila un pequeño programa C con make -j$(nproc). "
            "La compilación paralela es un caso de uso clásico de "
            "throughput: muchos procesos CPU-bound de corta duración. "
            "Un scheduler con buen balanceo reparte los procesos "
            "entre todos los núcleos disponibles, minimizando el "
            "tiempo total. Esta prueba ayudó a identificar el "
            "planificador óptimo durante el desarrollo de R.E.A.C.T.O.R."
        ),
    },
    "loaded": {
        "titulo": traducir("Latencia Bajo Carga"),
        "explicacion": traducir(
            "Mide la latencia de respuesta del sistema mientras está "
            "sometido a una carga CPU intensiva en segundo plano. "
            "hyperfine ejecuta un comando rápido mientras stress-ng "
            "satura la CPU, evaluando si el scheduler protege las "
            "tareas interactivas."
        ),
        "estudio": traducir(
            "Un valor bajo (µs) es mejor. La diferencia entre esta "
            "medición y la de 'Creación de Procesos' (sin carga) "
            "revela cuánto impacta la carga de fondo en la latencia "
            "de nuevas tareas. Idealmente debería ser mínima."
        ),
        "detalle": traducir(
            "Mientras stress-ng ejecuta hilos CPU-bound en todos los "
            "núcleos, hyperfine mide la latencia de fork+exec. "
            "Un scheduler con buena aislamiento de carga prioriza "
            "las tareas interactivas sobre las de fondo, manteniendo "
            "la capacidad de respuesta del sistema incluso cuando "
            "la CPU está al 100%. Es la prueba más representativa "
            "de la experiencia de usuario real en un sistema ocupado."
        ),
    },
}


def _seccion(tit, txt):
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    lbl_t = Gtk.Label(label=tit, css_classes=["heading"], xalign=0)
    lbl_c = Gtk.Label(label=txt, css_classes=["dim-label"], xalign=0, wrap=True)
    b.append(lbl_t)
    b.append(lbl_c)
    return b


def _armar_contenido(claves):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    for clave in claves:
        info = INFO_PRUEBAS.get(clave)
        if not info:
            continue

        cols = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tit = Gtk.Label(label=info["titulo"], css_classes=["title-3"], xalign=0)
        cols.append(tit)
        cols.append(_seccion(traducir("Qué mide"), info["explicacion"]))
        cols.append(_seccion(traducir("Cómo interpretarlo"), info["estudio"]))
        cols.append(_seccion(traducir("Detalle técnico"), info["detalle"]))
        box.append(cols)

    return box


def mostrar_info_grupo(win, claves, titulo_grupo):
    cols = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                   margin_start=16, margin_end=16, margin_top=16, margin_bottom=16)

    lbl_tit = Gtk.Label(label=titulo_grupo, css_classes=["title-2"], xalign=0)
    cols.append(lbl_tit)
    cols.append(Gtk.Separator())
    cols.append(_armar_contenido(claves))

    scrolled = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
    scrolled.set_child(cols)

    dialog = Adw.Dialog()
    dialog.set_child(scrolled)
    dialog.set_content_width(550)
    dialog.set_content_height(600)
    dialog.set_presentation_mode(Adw.DialogPresentationMode.BOTTOM_SHEET)

    dialog.present(win)
