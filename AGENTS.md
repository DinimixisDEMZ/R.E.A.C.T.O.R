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
- **Sistema de iconos portátil**: 
  - `utils/iconos.py` — constantes centralizadas para todos los 42 iconos + `registrar_ruta_iconos()`
  - `data/icons/scalable/{actions,status,devices,places,categories,mimetypes,legacy}/` — 43 SVGs (42 simbólicos + 1 fullcolor) de respaldo
  - 13 SVGs desde `icon-development-kit` (GNOME), 27 desde Adwaita del sistema, 2 desde hicolor/MoreWaita
  - `data/icons/reactor.gresource` — GResource compilado con todos los SVGs
  - Registro vía `Gio.Resource.load()` + `Gio.resources_register()` + `Gtk.IconTheme.add_resource_path()` en `utils/iconos.py`
  - Los iconos bundleados tienen prioridad sobre los del sistema (resource path > icon theme)
  - Reemplazados iconos `org.gnome.Settings-*` (no estándar) por equivalentes Adwaita:
    - `app.py:177` → `network-server-symbolic`
    - `rendimiento.py:145` → `input-mouse-symbolic`
    - `automatizacion.py:119` → `application-x-executable-symbolic`
    - `automatizacion.py:138,1049` → `applications-engineering-symbolic`
- **Split `ui/automatizacion.py`** (1074L) → package `ui/automatizacion/` con 4 submódulos: `__init__.py` (re-exports), `pesos.py`, `historial.py`, `deteccion.py`
- **`core/constantes.py`** — constantes compartidas: `VERSION`, `TEMP_UMBRAL_*`, `INTERVALO_FRAME_MS`, `PESOS_POR_DEFECTO`
- **`core/estado.py`** — 3 dataclasses: `BenchmarkState`, `MonitorState`, `AutoDetectionState`; reemplaza atributos planos de VentanaSimple
- **Tests automatizados** — 49 tests en `tests/` cubriendo `scoring`, `benchmark` (parser YAML), `database` (DB temporal) y `thermal` (sensor mock)
- **Mejora de excepciones** — 35 `except Exception` genéricos reemplazados por tipos específicos (`OSError`, `ValueError`, `subprocess.SubprocessError`, etc.) en 14 archivos
- **Scoring renombrado** — `fairness` → `waste` (ratio de desperdicio, bajo = mejor); migración DB incluida

### Active
- Ninguno
- **R.E.A.C.T.O.R v1.0** — Primera versión estable released

### Improvement Queue (próximas mejoras)
1. ✅ Tests automatizados
2. ✅ Split automatizacion.py
3. ✅ Fix god-object VentanaSimple
4. ✅ `pyproject.toml`
5. ✅ Fix duplicación y constantes
6. ✅ Mejorar excepciones

### Known Issues / Limitations
- No hay issues conocidos activos

### Key Files
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/pyproject.toml` — proyecto Python estándar (metadatos, deps, pytest/ruff config)
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/core/constantes.py` — VERSION, umbrales térmicos, intervalos, pesos por defecto
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/core/estado.py` — BenchmarkState, MonitorState, AutoDetectionState dataclasses
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
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/utils/iconos.py` — constantes de iconos portátiles + `registrar_ruta_iconos()`
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/data/icons/scalable/` — 43 SVGs de iconos bundleados (IDK + Adwaita)
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/data/icons/reactor.gresource` — GResource compilado
