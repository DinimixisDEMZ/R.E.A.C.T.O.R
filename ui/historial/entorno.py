"""
Página de entorno del historial — Muestra información del sistema y hardware.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from utils.helpers import make_lscpu_finder, parse_lscpu_numeric, parse_lscpu_cache


def _crear_pagina_entorno(win):
    import platform as _platform
    import os as _os

    pagina = Adw.PreferencesPage()
    versiones = getattr(win, 'versiones', {})

    id_distro = ""
    try:
        release_so = _platform.freedesktop_os_release()
        nombre_distro = release_so.get("PRETTY_NAME", release_so.get("NAME", _platform.system()))
        id_distro = release_so.get("ID", "").lower()
    except Exception:
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
    except Exception:
        pass

    kernel = versiones.get("kernel", "—")

    # ── Logo grande ──
    grupo_logo = Adw.PreferencesGroup()
    caja_logo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=4)
    icono_logo = f"distributor-logo-{id_distro}" if id_distro else "start-here-symbolic"
    logo = Gtk.Image(icon_name=icono_logo, pixel_size=190)
    logo.set_valign(Gtk.Align.CENTER)
    caja_logo.append(logo)
    grupo_logo.add(caja_logo)
    pagina.add(grupo_logo)

    # ── Chips informativos con tooltip ──
    grupo_chips = Adw.PreferencesGroup()
    caja_chips = Gtk.Box(spacing=8, margin_top=4, margin_bottom=6, margin_start=12, margin_end=12, halign=Gtk.Align.CENTER)

    datos_chips = [
        ("computer-symbolic", nombre_distro, f"Sistema operativo: {nombre_distro}"),
        ("system-run-symbolic", kernel, f"Kernel: {kernel}"),
        ("applications-engineering-symbolic", arquitectura, f"Arquitectura: {arquitectura}"),
        ("avatar-default-symbolic", nombre_host, f"Hostname: {nombre_host}"),
        ("preferences-system-time-symbolic", tiempo_act, f"Actividad desde: {tiempo_act}"),
    ]
    for icon, text, tip in datos_chips:
        chip = Gtk.Box(spacing=5, css_classes=["card", "pill"])
        chip.set_margin_top(2)
        chip.set_margin_bottom(2)
        chip.set_has_tooltip(True)
        chip.set_tooltip_text(tip)
        img = Gtk.Image(icon_name=icon, pixel_size=12)
        img.set_valign(Gtk.Align.CENTER)
        img.set_margin_start(8)
        chip.append(img)
        lbl = Gtk.Label(label=text, css_classes=["caption-heading"])
        lbl.set_margin_end(10)
        lbl.set_margin_top(4)
        lbl.set_margin_bottom(4)
        chip.append(lbl)
        caja_chips.append(chip)
    grupo_chips.add(caja_chips)
    pagina.add(grupo_chips)

    # ── Herramientas ──
    grupo_herramientas = Adw.PreferencesGroup(title="Herramientas")
    for icon, titulo, valor in [
        ("application-x-executable-symbolic", "scxctl", versiones.get("scxctl", "—")),
        ("utilities-terminal-symbolic", "stress-ng", versiones.get("stressng", "—")),
        ("applications-utilities-symbolic", "hyperfine", versiones.get("hyperfine", "—")),
    ]:
        fila = Adw.ActionRow(title=titulo, subtitle=valor or "—")
        fila.add_css_class("property")
        fila.set_icon_name(icon)
        grupo_herramientas.add(fila)
    pagina.add(grupo_herramientas)

    # ── Especificaciones Avanzadas de CPU ──
    import subprocess as _subprocess
    import json as _json
    import glob as _glob

    try:
        lscpu_crudo = _subprocess.run(["lscpu", "-J"], capture_output=True, text=True, timeout=2)
        datos_lscpu = _json.loads(lscpu_crudo.stdout) if lscpu_crudo.returncode == 0 and lscpu_crudo.stdout else None
    except Exception:
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
                title="Capacidades y Topología de Hardware",
                description="Visualización comparativa de las capacidades de la CPU."
            )
            radar = RadarChart()
            radar_frame = Gtk.Frame(css_classes=["card"])
            radar_frame.set_child(radar)
            radar_group.add(radar_frame)

            buscar = make_lscpu_finder(mapa_plano)
            valor_nucleos = buscar('cpu(s)', 'logical cpu(s)', 'cpus')
            valor_hilos = buscar('hilo(s) de procesamiento por núcleo', 'thread(s) per core', 'hilo(s) por núcleo', 'hilo')
            valor_freq = buscar('cpu mhz máx', 'cpu max mhz', 'velocidad máxima de cpu', 'max cpu mhz')
            if not valor_freq:
                valor_freq = buscar('cpu(s) factor de escala mhz', 'cpu mhz', 'mhz')
            valor_l3 = buscar('l3', 'l3 cache', 'caché l3')
            valor_l2 = buscar('l2', 'l2 cache', 'caché l2')
            valor_nucleos_socket = buscar('núcleo(s) por', 'core(s) per socket', 'core(s)', 'núcleos por', 'nucleo')
            if not valor_nucleos_socket:
                valor_sockets = buscar('"socket(s)"', 'socket(s)', 'sockets') or '1'
                if valor_nucleos and valor_sockets:
                    try:
                        nucleos_calculados = int(parse_lscpu_numeric(valor_nucleos)) // max(1, int(parse_lscpu_numeric(valor_sockets)))
                        if valor_hilos:
                            nucleos_calculados //= max(1, int(parse_lscpu_numeric(valor_hilos)))
                        valor_nucleos_socket = str(nucleos_calculados) if nucleos_calculados > 0 else None
                    except Exception:
                        pass

            crudo = [valor_nucleos, valor_hilos, valor_freq, valor_l3, valor_l2, valor_nucleos_socket]
            unidades_r = [e[2] for e in _RADAR_EJES]
            def a_float(valores, idx):
                if not valores: return 0.0
                return parse_lscpu_cache(valores) if idx in (3, 4) else parse_lscpu_numeric(valores)
            numeros_crudos = [a_float(v, i) for i, v in enumerate(crudo)]
            predeterminados = [16.0, 4.0, 4000.0, 32.0, 8.0, 8.0]
            maximos = [max(v * 1.5, d) for v, d in zip(numeros_crudos, predeterminados)]
            normalizados, labels = [], []
            for idx, (fv, mx) in enumerate(zip(numeros_crudos, maximos)):
                normalizados.append(min(1.0, fv / mx) if mx else 0.0)
                u = unidades_r[idx]
                if idx == 2 and fv:
                    labels.append(f"{fv/1000:.1f}{u}")
                elif idx in (3, 4) and fv:
                    labels.append(f"{fv:.1f}{u}" if fv < 10 else f"{int(fv)}{u}")
                else:
                    labels.append(f"{int(fv)}{u}" if fv else "?")
            radar.set_data(normalizados, labels, raw_values=numeros_crudos)

            pagina.add(radar_group)
        else:
            grupo_respaldo = Adw.PreferencesGroup(
                title="Capacidades y Topología de Hardware",
                description="Visualización comparativa de las capacidades de la CPU."
            )
            grupo_respaldo.add(Gtk.Label(
                label="Cairo no disponible — instala python3-cairo",
                css_classes=["dim-label"], margin_top=12, margin_bottom=12
            ))
            pagina.add(grupo_respaldo)

        # ── Especificaciones Avanzadas de CPU (detalle lscpu) ──
        detalle_group = Adw.PreferencesGroup(
            title="Especificaciones Avanzadas de CPU",
            description="Jerarquía técnica completa agrupada de lscpu."
        )
        pagina.add(detalle_group)

        pairs = [
            ("General y Arquitectura", "Modos, tamaños de dirección, orden de bytes",
             ["vulnerabilidad", "vulnerability", "mitigación", "mitigation"]),
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
                exp = Adw.ExpanderRow(title=name, subtitle=desc)
                for campo, data in clasificado[name]:
                    exp.add_row(Adw.ActionRow(title=campo, subtitle=str(data)))
                detalle_group.add(exp)

    # ── Procesador ──
    modelo_cpu = "—"
    try:
        with open("/proc/cpuinfo") as f:
            for linea in f:
                if linea.startswith("model name"):
                    modelo_cpu = linea.split(":", 1)[1].strip()
                    break
    except Exception:
        pass
    grupo_cpu = Adw.PreferencesGroup()
    fila_cpu = Adw.ActionRow(title=f"<b>{modelo_cpu}</b>", subtitle="Información del procesador")
    fila_cpu.set_icon_name("power-profile-performance-symbolic")
    fila_cpu.set_use_markup(True)
    grupo_cpu.add(fila_cpu)
    pagina.add(grupo_cpu)

    return pagina
