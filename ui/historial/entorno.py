"""
Página de entorno del historial — Muestra información del sistema y hardware.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from utils.i18n import traducir
from utils.lscpu import make_lscpu_finder, parse_lscpu_numeric, parse_lscpu_cache
from core.hardware import obtener_perfil
from widgets.legend import crear_chip_informativo


def _crear_pagina_entorno(win):
    import os as _os
    import platform as _platform

    pagina = Adw.PreferencesPage()
    versiones = getattr(win, 'versiones', {})

    try:
        release_so = _platform.freedesktop_os_release()
        nombre_distro = release_so.get("PRETTY_NAME", release_so.get("NAME", _platform.system()))
    except (OSError, ValueError):
        nombre_distro = _platform.system()
    arquitectura = _platform.machine()
    nombre_host = _platform.node()

    tiempo_act = "—"
    try:
        with open("/proc/uptime") as f:
            actividad = float(f.read().split()[0])
            dias = int(actividad // 86400)
            horas = int((actividad % 86400) // 3600)
            minutos = int((actividad % 3600) // 60)
            tiempo_act = f"{dias}d {horas}h {minutos}m"
    except (OSError, ValueError):
        pass

    kernel = versiones.get("kernel", "—")

    # ── Chips informativos con tooltip ──
    grupo_chips = Adw.PreferencesGroup()
    caja_chips = Gtk.Box(spacing=8, margin_top=4, margin_bottom=6, margin_start=12, margin_end=12, halign=Gtk.Align.CENTER)

    datos_chips = [
        ("computer-symbolic", nombre_distro, f"{traducir('Sistema operativo')}: {nombre_distro}"),
        ("system-run-symbolic", kernel, f"{traducir('Kernel')}: {kernel}"),
        ("applications-engineering-symbolic", arquitectura, f"{traducir('Arquitectura')}: {arquitectura}"),
        ("avatar-default-symbolic", nombre_host, f"{traducir('Hostname')}: {nombre_host}"),
        ("preferences-system-time-symbolic", tiempo_act, f"{traducir('Actividad desde')}: {tiempo_act}"),
    ]
    for icon, text, tip in datos_chips:
        chip = crear_chip_informativo(icon, text, tooltip=tip)
        caja_chips.append(chip)
    grupo_chips.add(caja_chips)
    pagina.add(grupo_chips)

    # ── Herramientas ──
    grupo_herramientas = Adw.PreferencesGroup(title=traducir("Herramientas"))
    for icon, titulo, valor in [
        ("application-x-executable-symbolic", "scxctl", versiones.get("scxctl") or "—"),
        ("utilities-terminal-symbolic", "stress-ng", versiones.get("stressng") or "—"),
        ("applications-utilities-symbolic", "hyperfine", versiones.get("hyperfine") or "—"),
    ]:
        fila = Adw.ActionRow(title=titulo, subtitle=valor or "—")
        fila.add_css_class("property")
        fila.set_icon_name(icon)
        grupo_herramientas.add(fila)

    sub_rt = traducir("No instalado")
    ruta_rt = "/tmp/rt-tests"
    if not _os.path.isdir(ruta_rt):
        appdir = _os.environ.get("APPDIR")
        bundle = _os.path.join(appdir, "usr/share/reactor/rt-tests") if appdir else None
        if bundle and _os.path.isdir(bundle):
            ruta_rt = bundle
    if _os.path.isdir(ruta_rt):
        try:
            with open(_os.path.join(ruta_rt, "Makefile")) as f:
                ver = ""
                for linea in f:
                    if linea.startswith("VERSION"):
                        ver = linea.split("=", 1)[1].strip()
                        break
            sub_rt = f"v{ver}" if ver else "disponible"
        except (OSError, ValueError):
            sub_rt = "disponible"
    else:
        sub_rt = "no encontrado"
    fila_rt = Adw.ActionRow(title="rt-tests", subtitle=sub_rt)
    fila_rt.add_css_class("property")
    fila_rt.set_icon_name("applications-engineering-symbolic")
    grupo_herramientas.add(fila_rt)

    pagina.add(grupo_herramientas)

    # ── Especificaciones Avanzadas de CPU ──
    import subprocess as _subprocess
    import json as _json
    import glob as _glob

    try:
        # LC_ALL=C es obligatorio: lscpu TRADUCE los nombres de sus campos según
        # el idioma del sistema. En español devuelve "Nombre del modelo" en vez
        # de "Model name" y "Hilo(s) de procesamiento por núcleo" en vez de
        # "Thread(s) per core", así que el parser no encontraba nada. Peor aún,
        # la búsqueda por subcadena enganchaba campos equivocados y la pestaña
        # mostraba cifras inventadas —en francés, "66 CPUs" a 66 MHz— sin dar
        # ningún error. La aplicación se traduce a seis idiomas: esto afectaba a
        # casi todos sus usuarios.
        entorno_c = {**_os.environ, "LC_ALL": "C", "LANG": "C", "LANGUAGE": "C"}
        lscpu_crudo = _subprocess.run(["lscpu", "-J"], capture_output=True, text=True,
                                      timeout=5, env=entorno_c)
        datos_lscpu = _json.loads(lscpu_crudo.stdout) if lscpu_crudo.returncode == 0 and lscpu_crudo.stdout else None
    except (_subprocess.SubprocessError, _json.JSONDecodeError, OSError):
        datos_lscpu = None

    if datos_lscpu and 'lscpu' in datos_lscpu:
        mapa_plano = {}
        lista_campos = []
        def recorrer(entradas):
            for entrada in entradas:
                campo = entrada.get("field", "").rstrip(":")
                data = entrada.get("data")
                if data is not None:
                    lista_campos.append((campo, data))
                    mapa_plano[campo.lower()] = data
                hijos = entrada.get("children")
                if hijos: recorrer(hijos)
        recorrer(datos_lscpu['lscpu'])

        # ── Radar Chart ──
        from widgets.radar import _HAS_CAIRO, RadarChart, _EJES as _RADAR_EJES
        if _HAS_CAIRO:
            radar_group = Adw.PreferencesGroup(
                title=traducir("Capacidades y Topología de Hardware"),
                description=traducir("Visualización comparativa de las capacidades de la CPU.")
            )
            radar = RadarChart()
            radar_frame = Gtk.Frame(css_classes=["card"])
            radar_frame.set_child(radar)
            radar_group.add(radar_frame)

            # Los datos del radar salen de /sys, no de lscpu. Además de ser
            # inmunes al idioma, incluyen cosas que lscpu no distingue bien,
            # como el reparto de núcleos P/E en los CPU híbridos.
            perfil = obtener_perfil()
            caches = perfil.caches

            numeros_crudos = [
                float(perfil.hilos),
                float(perfil.hilos_por_nucleo),
                perfil.frecuencia_maxima_khz / 1000.0,   # kHz → MHz
                caches.get("l3", 0) / 1024.0,            # KiB → MiB
                caches.get("l2", 0) / 1024.0,
                float(perfil.nucleos_fisicos),
            ]

            # El eje se escala al equipo. Con máximos fijos, una máquina de 2
            # núcleos dibujaba una figura degenerada y una de 128 la saturaba.
            predeterminados = [16.0, 4.0, 5000.0, 32.0, 8.0, 8.0]
            maximos = [max(v * 1.5, d) for v, d in zip(numeros_crudos, predeterminados)]
            unidades_r = [e[2] for e in _RADAR_EJES]

            normalizados, labels = [], []
            for idx, (valor, maximo) in enumerate(zip(numeros_crudos, maximos)):
                normalizados.append(min(1.0, valor / maximo) if maximo else 0.0)
                unidad = unidades_r[idx]
                if idx == 2 and valor:
                    labels.append(f"{valor / 1000:.1f}{unidad}")
                elif idx in (3, 4) and valor:
                    labels.append(f"{valor:.1f}{unidad}" if valor < 10 else f"{int(valor)}{unidad}")
                else:
                    labels.append(f"{int(valor)}{unidad}" if valor else "?")

            radar.set_data(normalizados, labels, raw_values=numeros_crudos)

            pagina.add(radar_group)
        else:
            grupo_respaldo = Adw.PreferencesGroup(
                title=traducir("Capacidades y Topología de Hardware"),
                description=traducir("Visualización comparativa de las capacidades de la CPU.")
            )
            grupo_respaldo.add(Gtk.Label(
                label=traducir("Cairo no disponible — instala python3-cairo"),
                css_classes=["dim-label"], margin_top=12, margin_bottom=12
            ))
            pagina.add(grupo_respaldo)

        # ── Especificaciones Avanzadas de CPU (detalle lscpu) ──
        detalle_group = Adw.PreferencesGroup(
            title=traducir("Especificaciones Avanzadas de CPU"),
            description=traducir("Jerarquía técnica completa agrupada de lscpu.")
        )
        pagina.add(detalle_group)

        pairs = [
            ("General y Arquitectura", "Modos, tamaños de dirección, orden de bytes",
             []),
            ("Topología y Distribución", "Hilos por núcleo, núcleos por socket, sockets, NUMA",
             ["socket", "nodo", "numa", "hilo", "núcleo", "core", "thread", "siblings", "cpu(s)"]),
            ("Cachés de CPU", "Jerarquía de memorias caché L1d, L1i, L2 y L3",
             ["l1", "l2", "l3", "l1d", "l1i", "caché", "cache"]),
            ("Frecuencias y Escalado", "Frecuencias mín/máx, BogoMIPS y factor de escala",
             ["mhz", "ghz", "frecuencia", "frequency", "bogomips", "escala", "scaling", "driver", "aumento"]),
            ("Virtualización e Hipervisor", "Soporte y tipo de virtualización por hardware",
             ["virtual", "hiper", "hyper", "kvm"]),
            ("Mitigaciones de Seguridad", "Vulnerabilidades de CPU y su estado de mitigación",
             ["vulnerabilidad", "vulnerability", "mitigación", "mitigation", "gather data", "ghostwrite", "speculative"]),
        ]

        clasificado = {name: [] for name, _, _ in pairs}
        for campo, data in lista_campos:
            campo_min = campo.lower()
            agregado = False
            for name, _, keywords in pairs:
                if any(k in campo_min for k in keywords):
                    clasificado[name].append((campo, data))
                    agregado = True
                    break
            if not agregado:
                clasificado["General y Arquitectura"].append((campo, data))

        for name, desc, _ in pairs:
            if clasificado[name]:
                exp = Adw.ExpanderRow(title=traducir(name), subtitle=traducir(desc))
                for campo, data in clasificado[name]:
                    exp.add_row(Adw.ActionRow(title=campo, subtitle=str(data)))
                detalle_group.add(exp)

    # ── Procesador ──
    perfil_equipo = obtener_perfil()
    modelo_cpu = perfil_equipo.modelo or "—"
    grupo_cpu = Adw.PreferencesGroup()
    # El texto del modelo viene del hardware y puede traer caracteres que Pango
    # interprete como marcado; se escapa antes de insertarlo.
    fila_cpu = Adw.ActionRow(
        title=f"<b>{GLib.markup_escape_text(modelo_cpu)}</b>",
        subtitle=perfil_equipo.descripcion_corta())
    fila_cpu.set_icon_name("power-profile-performance-symbolic")
    fila_cpu.set_use_markup(True)
    grupo_cpu.add(fila_cpu)
    pagina.add(grupo_cpu)

    return pagina
