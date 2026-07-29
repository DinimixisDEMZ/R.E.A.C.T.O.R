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

### v0.8 — Auditoría y refactorización completa
- **4 bugs críticos corregidos**: check_same_thread, subprocess import, set_sensitive, re-entrancy guard
- **9 bugs altos corregidos**: sensor timer, HTML release_notes, scheduler names traducidos, log tags, etc.
- **8 bugs medios corregidos**: 4 memory leaks de timers, crosshair chart, lscpu keywords, dead code, etc.
- **God functions refactorizadas**: iniciar_auto_test (208→18L), _refrescar_tendencia (272→12L), iniciar_verificacion (125→25L), sincronizar_sistema (41→15L)
- **10 violaciones DRY**: ~285 líneas eliminadas (chips, dots, child-removal, logging, toasts, result dict)
- **AppImage autocontenido**: cyclictest estático + rt-tests source bundleado, versión desde git tag
- **57 tests** (49 + 8 nuevos para hybrid.py y calcular_score_categorias)
- **Eliminado Nuitka** (build.sh), puro AppImage
- **pyproject.toml**: backend build_meta, version dinámica
- **Archivos muertos**: design/ (7 archivos) + .po~ eliminados

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
- `pyproject.toml` — proyecto Python estándar (metadatos, deps, pytest/ruff config)
- `core/constantes.py` — VERSION, umbrales térmicos, intervalos, pesos por defecto
- `core/estado.py` — EstadoPrueba, EstadoMonitor, EstadoDeteccionAuto dataclasses
- `core/scoring.py` — `calcular_scores_finales()`, `calcular_score_categorias()`
- `core/benchmark.py` — benchmark con stress-ng (cpu, threads, memory)
- `core/hybrid.py` — benchmark con hyperfine (fork, compile, loaded)
- `core/database.py` — persistencia SQLite, compatibilidad, migraciones
- `core/verificacion.py` — motor de verificación de sistema + AppImage
- `ui/automatizacion/` — detección automática del mejor scheduler
  - `__init__.py` — re-exports, configuración de pesos
  - `deteccion.py` — loop de tests, gestión térmica, scores
  - `pesos.py` — sliders, presets, animaciones
  - `historial.py` — navegación de runs automáticos
- `ui/disponibilidad.py` — verificación BPF scheduler por scheduler
- `ui/historial/` — plantilla modular del historial
  - `pagina.py` — setup, InLineViewSwitcher
  - `tendencia.py` — gráfico + tabla ColumnView + chips
  - `resultados.py` — lista de resultados con filtros
  - `dibujo.py` — Cairo rendering, hover tooltip, crossfade
  - `entorno.py` — info del sistema, hardware, radar lscpu
  - `constantes.py` — tipos de prueba y rangos de fecha
- `ui/diagnostico/` — monitoreo en vivo
  - `pagina.py` — setup, scxtop embebido
  - `monitoreo.py` — CPU/mem/temp/nucleos/planif/eventos
- `widgets/radar.py` — RadarChart (Cairo) compartido
- `widgets/legend.py` — chips de leyenda interactivos
- `widgets/circular_meter.py` — medidor circular térmico
- `utils/helpers.py` — logging, colores, lscpu, bundle detection, format_raw_value, dibujar_dot, vaciar_contenedor, toast
- `utils/iconos.py` — constantes de iconos portátiles + GResource
- `utils/i18n.py` — internacionalización gettext
- `data/icons/reactor.gresource` — GResource compilado con SVGs bundleados
