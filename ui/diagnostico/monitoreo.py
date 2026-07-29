"""
Monitor tab: real-time CPU, memory, temperature, core usage, and scheduler metrics.
Contains all helper/monitoring functions and the live-update callback.
"""

import glob
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

from utils.helpers import obtener_color_tema
from widgets.circular_meter import CircularMeter, _color_para_temperatura
from core.constantes import CARGANDO
from utils.i18n import traducir


# ── Lectura de datos del Sistema (/proc) ──────────────────────────────────────

def obtener_uso_cpu_general():
    try:
        with open("/proc/stat", "r") as f:
            linea = f.readline()
        if linea.startswith("cpu "):
            partes = linea.split()
            inactivo = float(partes[4]) + float(partes[5])  # idle + iowait
            no_inactivo = sum(float(x) for x in [partes[1], partes[2], partes[3], partes[6], partes[7], partes[8]])
            return inactivo + no_inactivo, inactivo
    except (OSError, ValueError):
        pass
    return 0.0, 0.0


def obtener_uso_nucleos():
    nucleos = {}
    try:
        with open("/proc/stat", "r") as f:
            for linea in f:
                if linea.startswith("cpu") and linea[3].isdigit():
                    partes = linea.split()
                    nombre = partes[0]
                    inactivo = float(partes[4]) + float(partes[5])
                    no_inactivo = sum(float(x) for x in [partes[1], partes[2], partes[3], partes[6], partes[7], partes[8]])
                    nucleos[nombre] = (inactivo + no_inactivo, inactivo)
    except (OSError, ValueError):
        pass
    return nucleos


def obtener_uso_memoria():
    try:
        with open("/proc/meminfo", "r") as f:
            lineas = f.readlines()
        mem = {}
        for linea in lineas:
            partes = linea.split(":")
            if len(partes) == 2:
                mem[partes[0].strip()] = int(partes[1].replace("kB", "").strip())

        total = mem.get("MemTotal", 0)
        disponible = mem.get("MemAvailable", 0)
        if total > 0:
            usado = total - disponible
            fraccion = usado / total
            return total / 1024 / 1024, usado / 1024 / 1024, fraccion
    except (OSError, ValueError):
        pass
    return 0.0, 0.0, 0.0


def obtener_carga_media():
    try:
        with open("/proc/loadavg", "r") as f:
            partes = f.read().split()
        return partes[0], partes[1], partes[2]
    except (OSError, ValueError):
        return "0.00", "0.00", "0.00"


def obtener_estadisticas_planif():
    ctxt = 0
    ejecutando = 0
    bloqueado = 0
    try:
        with open("/proc/stat", "r") as f:
            for linea in f:
                if linea.startswith("ctxt"):
                    ctxt = int(linea.split()[1])
                elif linea.startswith("procs_running"):
                    ejecutando = int(linea.split()[1])
                elif linea.startswith("procs_blocked"):
                    bloqueado = int(linea.split()[1])
    except (OSError, ValueError):
        pass
    return ctxt, ejecutando, bloqueado


def _leer_proc_cpuinfo():
    info = []
    try:
        with open('/proc/cpuinfo', 'r') as f:
            contenido = f.read()
        for b in contenido.strip().split('\n\n'):
            props = {}
            for l in b.splitlines():
                if ':' in l:
                    k, v = l.split(':', 1)
                    props[k.strip()] = v.strip()
            if props:
                info.append(props)
    except (OSError, ValueError):
        pass
    return info


# ── Monitoreo en Tiempo Real - Callback de Actualización ──────────────────────

def actualizar_diagnostico_tiempo_real(win, controles):
    """Callback periódico para actualizar métricas en vivo."""
    try:
        # Detener actualización si la ventana ya no está visible o fue destruida
        if not win or not win.get_visible():
            return False
    except (OSError, AttributeError):
        return False

    # Evitar consumo innecesario si no estamos en la pestaña Diagnóstico
    if win.split_view.get_content() != win.pag_diagnostico:
        return True

    # 1. Carga General de CPU
    t_total, t_inactivo = obtener_uso_cpu_general()
    if win.monitor_state.prev_cpu_total is not None:
        d_total = t_total - win.monitor_state.prev_cpu_total
        d_inactivo = t_inactivo - win.monitor_state.prev_cpu_idle
        if d_total > 0:
            uso_cpu = (d_total - d_inactivo) / d_total
            controles["medidor_cpu"].update(uso_cpu, f"{uso_cpu * 100:.1f}%")
    win.monitor_state.prev_cpu_total = t_total
    win.monitor_state.prev_cpu_idle = t_inactivo

    # 2. Uso de Memoria
    m_total, m_usado, m_fraccion = obtener_uso_memoria()
    if m_total > 0:
        controles["medidor_mem"].update(m_fraccion, f"{m_usado:.1f} GB")

    # 3. Temperatura
    t_temp = win.sensor.obtener_temp()
    if t_temp > 0:
        controles["medidor_temp"].update(t_temp / 100.0, f"{t_temp:.1f} °C", color=_color_para_temperatura(t_temp))
    else:
        controles["medidor_temp"].update(0.0, traducir("N/D"))

    # 4. Planificador Activo
    nombre_sc, modo_sc = win.scx.obtener_estado()
    btn_planif = controles["lbl_valor_planif"]
    btn_planif.remove_css_class("success")
    btn_planif.remove_css_class("destructive-action")
    if nombre_sc:
        btn_planif.set_label(f"{nombre_sc} [{modo_sc}]")
        btn_planif.add_css_class("success")
    else:
        btn_planif.set_label(traducir("Planificador del Sistema"))
        btn_planif.add_css_class("destructive-action")

    # 5. Carga de Cores Individuales
    estadisticas_nucleos = obtener_uso_nucleos()
    for nombre, (c_total, c_inactivo) in estadisticas_nucleos.items():
        if nombre in win.monitor_state.prev_cores:
            total_anterior, inactivo_anterior = win.monitor_state.prev_cores[nombre]
            d_total = c_total - total_anterior
            d_inactivo = c_inactivo - inactivo_anterior
            if d_total > 0:
                uso_nucleo = (d_total - d_inactivo) / d_total
                if nombre in controles["barras_nucleo"]:
                    controles["barras_nucleo"][nombre].set_fraction(uso_nucleo)
                    controles["etiquetas_nucleo"][nombre].set_label(f"{int(uso_nucleo * 100)}%")
        win.monitor_state.prev_cores[nombre] = (c_total, c_inactivo)

    # 6. Estadísticas de Planificación
    ctxt, ejecutando, bloqueado = obtener_estadisticas_planif()
    ahora_t = time.time()
    if win.monitor_state.prev_ctxt is not None and win.monitor_state.prev_ctxt_time is not None:
        dt = ahora_t - win.monitor_state.prev_ctxt_time
        if dt > 0:
            tasa_ctxt = (ctxt - win.monitor_state.prev_ctxt) / dt
            # Mostrar con formato local (puntos de miles)
            controles["lbl_tasa_ctxt"].set_label(f"{int(tasa_ctxt):,}".replace(",", ".") + traducir(" ctxt/s"))
    win.monitor_state.prev_ctxt = ctxt
    win.monitor_state.prev_ctxt_time = ahora_t

    controles["lbl_ctxt_total"].set_label(f"{ctxt:,}".replace(",", "."))
    controles["lbl_procs_ejecutando"].set_label(str(ejecutando))
    controles["lbl_procs_bloqueados"].set_label(str(bloqueado))

    # Resaltar en rojo si hay procesos bloqueados por I/O
    controles["lbl_procs_bloqueados"].remove_css_class("error-label")
    controles["lbl_procs_bloqueados"].remove_css_class("success-label")
    if bloqueado > 0:
        controles["lbl_procs_bloqueados"].add_css_class("error-label")
    else:
        controles["lbl_procs_bloqueados"].add_css_class("success-label")

    # 7. Carga Media (Load Average)
    cm1, cm5, cm15 = obtener_carga_media()
    controles["lbl_carga_media"].set_label(f"{cm1}  •  {cm5}  •  {cm15}")

    return True


# ── Construcción del Tab Monitor ──────────────────────────────────────────────

def configurar_pestana_monitor(win):
    """Create the Monitor preferences page with all real-time content.
    Returns (pagina_pref, controles_dict)."""
    pagina_pref = Adw.PreferencesPage()

    # ── CSS Personalizado para la Rejilla ──
    proveedor_css = Gtk.CssProvider()
    proveedor_css.load_from_data("""
        .core-card {
            background-color: alpha(@window_bg_color, 0.45);
            border: 1px solid alpha(@border_color, 0.35);
            border-radius: 8px;
            padding: 6px 10px;
        }
        .core-label {
            font-size: 8.5pt;
            font-weight: bold;
            color: alpha(@window_fg_color, 0.85);
            min-width: 45px;
        }
        .core-pct-label {
            font-size: 8pt;
            color: alpha(@window_fg_color, 0.7);
            min-width: 32px;
        }
        .success-label {
            color: #26a269;
            font-weight: bold;
        }
        .warning-label {
            color: #e5a50a;
            font-weight: bold;
        }
        .error-label {
            color: #c01c28;
            font-weight: bold;
        }
    """, -1)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        proveedor_css,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    # ── Estado de Monitoreo en el Objeto Window ──
    win.monitor_state.prev_cpu_total = None
    win.monitor_state.prev_cpu_idle = None
    win.monitor_state.prev_cores = {}
    win.monitor_state.prev_ctxt = None
    win.monitor_state.prev_ctxt_time = None

    # ── 1. Medidores en Tiempo Real (Grandes, al principio) ──
    grupo_medidores = Adw.PreferencesGroup()
    medidor_cpu = CircularMeter("power-profile-performance-symbolic", traducir("CPU"), size=90)
    medidor_mem = CircularMeter("drive-harddisk-symbolic", traducir("RAM"), size=90)
    medidor_temp = CircularMeter("weather-clear-symbolic", traducir("Temp"), size=90)

    caja_medidores = Gtk.Box(spacing=24, halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=12)
    caja_medidores.append(medidor_cpu)
    caja_medidores.append(medidor_mem)
    caja_medidores.append(medidor_temp)
    grupo_medidores.add(caja_medidores)
    pagina_pref.add(grupo_medidores)

    # ── 2. Encabezado de la Página ──
    grupo_banner = Adw.PreferencesGroup()
    fila_titulo_cpu = Adw.ActionRow()
    fila_titulo_cpu.set_icon_name("power-profile-performance-symbolic")
    grupo_banner.add(fila_titulo_cpu)
    pagina_pref.add(grupo_banner)
    win.fila_titulo_cpu = fila_titulo_cpu

    # Poblar nombre de CPU desde lscpu
    def _poblar_cpu():
        try:
            info_cpu = _leer_proc_cpuinfo()
            primero = info_cpu[0] if info_cpu else {}
            modelo = primero.get('model name', '')
            if modelo:
                win.fila_titulo_cpu.set_title(f"<b>{modelo}</b>")
                win.fila_titulo_cpu.set_subtitle(traducir("Información y Diagnóstico de la CPU"))
                win.fila_titulo_cpu.set_use_markup(True)
        except (OSError, ValueError):
            pass
        return False
    GLib.idle_add(_poblar_cpu)

    # ── 3. Monitoreo en Tiempo Real ──
    grupo_rt = Adw.PreferencesGroup(
        title=traducir("Monitoreo en Tiempo Real"),
        description=traducir("Estado, carga del sistema e integridad térmica.")
    )

    fila_planif = Adw.ActionRow(title=traducir("Planificador Activo"))
    lbl_valor_planif = Gtk.Button(label=traducir(CARGANDO), valign=Gtk.Align.CENTER, css_classes=["flat"])
    fila_planif.add_suffix(lbl_valor_planif)
    grupo_rt.add(fila_planif)

    # Rejilla de carga por núcleo lógica
    nucleos_iniciales = obtener_uso_nucleos()
    nombres_nucleos = sorted(nucleos_iniciales.keys(), key=lambda x: int(x[3:]) if x[3:].isdigit() else 0)

    caja_flujo = Gtk.FlowBox(
        valign=Gtk.Align.START,
        max_children_per_line=8,
        min_children_per_line=2,
        selection_mode=Gtk.SelectionMode.NONE,
        homogeneous=True,
        row_spacing=6,
        column_spacing=6
    )

    barras_nucleo = {}
    etiquetas_nucleo = {}

    for nombre in nombres_nucleos:
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        card.add_css_class("core-card")

        lbl_num = Gtk.Label(label=traducir("CPU {}").format(nombre[3:]))
        lbl_num.add_css_class("core-label")
        lbl_num.set_halign(Gtk.Align.START)

        barra = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True)
        barra.add_css_class("core-progress")

        lbl_pct = Gtk.Label(label=traducir("0%"))
        lbl_pct.add_css_class("core-pct-label")
        lbl_pct.set_halign(Gtk.Align.END)

        card.append(lbl_num)
        card.append(barra)
        card.append(lbl_pct)
        caja_flujo.append(card)

        barras_nucleo[nombre] = barra
        etiquetas_nucleo[nombre] = lbl_pct

    if nombres_nucleos:
        expandidor_nucleos = Adw.ExpanderRow(
            title=traducir("Carga por Núcleo de Procesamiento"),
            subtitle=traducir("Uso en tiempo real de cada CPU lógica")
        )
        contenedor_caja_flujo = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=10, margin_bottom=10, margin_start=12, margin_end=12
        )
        contenedor_caja_flujo.append(caja_flujo)
        expandidor_nucleos.add_row(contenedor_caja_flujo)
        grupo_rt.add(expandidor_nucleos)

    pagina_pref.add(grupo_rt)

    # ── 3. Métricas de Planificación ──
    grupo_planif = Adw.PreferencesGroup(
        title=traducir("Métricas de Planificación"),
        description=traducir("Agilidad y comportamiento de la cola de tareas del Kernel Linux.")
    )

    fila_carga_media = Adw.ActionRow(title=traducir("Carga Media (Load Average)"))
    lbl_carga_media = Gtk.Label(label=traducir(CARGANDO), valign=Gtk.Align.CENTER)
    fila_carga_media.add_suffix(lbl_carga_media)
    grupo_planif.add(fila_carga_media)

    fila_tasa_ctxt = Adw.ActionRow(title=traducir("Cambios de Contexto (Context Switches)"))
    lbl_tasa_ctxt = Gtk.Label(label=traducir("Calculando..."), valign=Gtk.Align.CENTER)
    fila_tasa_ctxt.add_suffix(lbl_tasa_ctxt)
    grupo_planif.add(fila_tasa_ctxt)

    fila_ctxt_total = Adw.ActionRow(title=traducir("Cambios de Contexto Totales (desde arranque)"))
    lbl_ctxt_total = Gtk.Label(label=traducir(CARGANDO), valign=Gtk.Align.CENTER)
    fila_ctxt_total.add_suffix(lbl_ctxt_total)
    grupo_planif.add(fila_ctxt_total)

    fila_procs_ejecutando = Adw.ActionRow(title=traducir("Tareas en Ejecución Activa"))
    lbl_procs_ejecutando = Gtk.Label(label=traducir(CARGANDO), valign=Gtk.Align.CENTER)
    fila_procs_ejecutando.add_suffix(lbl_procs_ejecutando)
    grupo_planif.add(fila_procs_ejecutando)

    fila_procs_bloqueados = Adw.ActionRow(
        title=traducir("Tareas Bloqueadas (Esperando I/O)"),
        subtitle=traducir("Un valor elevado indica cuellos de botella en el disco o red")
    )
    lbl_procs_bloqueados = Gtk.Label(label=traducir(CARGANDO), valign=Gtk.Align.CENTER)
    fila_procs_bloqueados.add_suffix(lbl_procs_bloqueados)
    grupo_planif.add(fila_procs_bloqueados)

    pagina_pref.add(grupo_planif)

    # ── Eventos del Scheduler (sysfs, sin root) ──
    grupo_eventos_scx = Adw.PreferencesGroup(
        title=traducir("Eventos de sched_ext"),
        description=traducir("Contadores del planificador BPF activo — lecturas de /sys/kernel/sched_ext/")
    )
    try:
        ops = open("/sys/kernel/sched_ext/root/ops").read().strip()
    except (OSError, ValueError):
        ops = "—"
    fila_nombre_scx = Adw.ActionRow(title=traducir("Planificador Activo"))
    lbl_nombre_scx = Gtk.Label(label=ops, valign=Gtk.Align.CENTER, css_classes=["caption-heading"])
    fila_nombre_scx.add_suffix(lbl_nombre_scx)
    grupo_eventos_scx.add(fila_nombre_scx)

    try:
        estado = open("/sys/kernel/sched_ext/state").read().strip()
    except (OSError, ValueError):
        estado = "—"
    fila_estado_scx = Adw.ActionRow(title=traducir("Estado"))
    lbl_estado_scx = Gtk.Label(label=estado, valign=Gtk.Align.CENTER)
    fila_estado_scx.add_suffix(lbl_estado_scx)
    grupo_eventos_scx.add(fila_estado_scx)

    try:
        secuencia = open("/sys/kernel/sched_ext/enable_seq").read().strip()
    except (OSError, ValueError):
        secuencia = "—"
    fila_secuencia_scx = Adw.ActionRow(title=traducir("Secuencia de Activación"))
    lbl_secuencia_scx = Gtk.Label(label=secuencia, valign=Gtk.Align.CENTER)
    fila_secuencia_scx.add_suffix(lbl_secuencia_scx)
    grupo_eventos_scx.add(fila_secuencia_scx)

    try:
        rechazado = open("/sys/kernel/sched_ext/nr_rejected").read().strip()
    except (OSError, ValueError):
        rechazado = "—"
    fila_rechazados_scx = Adw.ActionRow(title=traducir("Tareas Rechazadas"))
    lbl_rechazados_scx = Gtk.Label(label=rechazado, valign=Gtk.Align.CENTER)
    fila_rechazados_scx.add_suffix(lbl_rechazados_scx)
    grupo_eventos_scx.add(fila_rechazados_scx)

    try:
        archivos_eventos = glob.glob("/sys/kernel/sched_ext/*/events")
        lineas_eventos = []
        for ae in archivos_eventos:
            for linea in open(ae).read().strip().splitlines():
                partes = linea.split(None, 1)
                if len(partes) == 2:
                    nombre = partes[0].replace("SCX_EV_", "").replace("_", " ").title()
                    lineas_eventos.append((nombre, partes[1]))
        lineas_eventos.sort(key=lambda x: -int(x[1]) if x[1].isdigit() else 0)
        for nombre_ev, valor_ev in lineas_eventos[:10]:
            fila = Adw.ActionRow(title=nombre_ev)
            lbl = Gtk.Label(label=valor_ev, valign=Gtk.Align.CENTER, css_classes=["monospace"])
            fila.add_suffix(lbl)
            grupo_eventos_scx.add(fila)
    except (OSError, ValueError):
        pass

    pagina_pref.add(grupo_eventos_scx)

    # ── Diccionario de Widgets para el Monitoreo en Vivo ──
    controles = {
        "medidor_cpu": medidor_cpu,
        "medidor_mem": medidor_mem,
        "medidor_temp": medidor_temp,
        "lbl_valor_planif": lbl_valor_planif,
        "barras_nucleo": barras_nucleo,
        "etiquetas_nucleo": etiquetas_nucleo,
        "lbl_tasa_ctxt": lbl_tasa_ctxt,
        "lbl_ctxt_total": lbl_ctxt_total,
        "lbl_procs_ejecutando": lbl_procs_ejecutando,
        "lbl_procs_bloqueados": lbl_procs_bloqueados,
        "lbl_carga_media": lbl_carga_media,
    }

    return pagina_pref, controles
