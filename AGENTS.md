# Reactor Project — Agent Session Summary

## Project
Reactor (Reactor Experimental Avanzado Concurrente Telúrico para Optimización de Rendimiento) — A CPU scheduler benchmarking and optimization tool for Linux with `scx` schedulers.

## Hardware
- Lenovo V15 G4 ABP, AMD Ryzen 7 7730U (16 threads), 16.80 GB RAM
- Solus 4.9, GNOME 50.3, kernel 7.1.3-347.current
- No `sudo`; user runs `run0` for root operations

## Entry Point
`main.py`

## Current State

### Recently completed
- **Sliders redesigned** — vertical layout with name + percentage header (heading/accent) above, slider with draw_value=True below
- **Real-time redistribution** — dragging one slider proportionally adjusts the other two to keep total=100 instantly, no debounce delay
- **Preset buttons** — "Balanceado" (45/45/10), "Potencia" (70/20/10), "Respuesta" (10/70/20), "Fluidez" (10/20/70) as icon ToggleButtons, replacing old reset button
- **`_aplicar_preset`** — animates sliders to any preset, deactivates sibling buttons, triggers ranking recalc
- **Adw.Banner** — "Recalculando…" uses native Adw.Banner instead of Gtk.Revealer
- **Header icon presets** — moved preset buttons to header as circular icon buttons
- **Checkbox scheduler selection** for auto-detection
- **p95 fix** for `memory` benchmark type
- **Time-travel navigation** (prev/next buttons) in auto-detection header
- **Reorganización modular**: `ui/historial/` y `ui/diagnóstico/` con subdirectorios
- **RadarChart extraído** a `widgets/radar.py` (compartido entre Monitor y Entorno)
- **Helpers compartidos** en `utils/helpers.py`
- **Traducción a español**: funciones, variables, constantes en todos los módulos
- **Tabla comparativa con Gtk.ColumnView nativo**:
  - `Gtk.CustomSorter()` + `set_sort_func()` (con `*args` para PyGObject user_data)
  - Pipeline: `ListStore → SortListModel(ColumnView.sorter) → MultiSelection`
  - Resaltado "mejor" dinámico por columna (sin flags pre-computados)
  - Desviación estándar con Bessel (N-1)
- **scxtop lifecycle**: kill/re-spawn con `stack.connect("notify::visible-child")`
- **Eventos sched_ext** desde sysfs sin root
- **Iconos Adwaita verificados** y duplicados eliminados
- **InLineViewSwitcher** en Historial y Diagnóstico (navegación tipo pestañas)

### Active
- Ninguno
- Tareas pendientes: ninguna en este momento

### Known Issues / Limitations
- No hay issues conocidos activos

### Key Files
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/core/scoring.py` — `calcular_scores_finales()` accepts `pesos` param
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/core/benchmark.py` — p95 reads `nanosecs-per-mutex` for memory type
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/core/database.py` — `consultar_runs_auto()` and `cargar_resultados_de_run()`
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/ui/automatizacion.py` — scheduler checklist, weight sliders with presets, history nav
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/ui/disponibilidad.py` — `recargar_disponibilidad_ui` non-destructive refresh
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/ui/historial/` — plantilla modular del historial
  - `pagina.py` — setup, InLineViewSwitcher
  - `tendencia.py` — gráfico + tabla ColumnView
  - `resultados.py` — lista de resultados
  - `dibujo.py` — Cairo rendering, hover tooltip, fade-out
  - `entorno.py` — info del sistema, hardware, radar
  - `constantes.py` — tipos de prueba y rangos de fecha
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/ui/diagnostico/` — plantilla modular de diagnóstico
  - `pagina.py` — setup, InLineViewSwitcher
  - `monitoreo.py` — monitor en vivo CPU/mem/temp/nucleos/planif/eventos
  - `scxtop.py` — terminal embebido scxtop
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/widgets/radar.py` — RadarChart compartido
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/utils/helpers.py` — parse_lscpu_*, make_lscpu_finder, generar_color_hash, obtener_color_tema
