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
- **Checkbox scheduler selection** for auto-detection — `_refrescar_auto_schedulers(win)` populates checkable `Adw.ActionRow` list in `grupo_auto`, below the "Determinar" button
- **Sliders now independent** — no more auto-normalization on value-changed; normalization only in `_poblar_ranking` at calculation time
- **`_restaurar_pesos` uses `_ajustando_pesos` flag** to suppress value-changed signals during bulk slider reset
- **Div-by-zero protection** in `_poblar_ranking` when all sliders at 0
- **Dev mode toggle** resets `compatibles=None`, clears nav badge, rebuilds disponibilidad + auto scheduler lists
- **Time-travel navigation** (prev/next buttons) in auto-detection header
- **p95 fix** for `memory` benchmark type (nanosecs-per-mutex extraction)

### Active
- `_refrescar_auto_schedulers` called on startup, after compatibility verification, and after dev mode toggle
- `motor()` in `iniciar_auto_test` reads only checked schedulers

### Known Issues / Limitations
- History restore works from DB only for `run_type='auto'` runs
- No manual reorder of schedulers in the checklist

### Key Files
- `/home/dinimixis/Documentos/Proyectos/Reactor/core/scoring.py` — `calcular_scores_finales()` accepts `pesos` param
- `/home/dinimixis/Documentos/Proyectos/Reactor/core/benchmark.py` — p95 reads `nanosecs-per-mutex` for memory type
- `/home/dinimixis/Documentos/Proyectos/Reactor/ui/automatizacion.py` — scheduler checklist, weight sliders, history nav
- `/home/dinimixis/Documentos/Proyectos/Reactor/ui/disponibilidad.py` — `recargar_disponibilidad_ui` non-destructive refresh
- `/home/dinimixis/Documentos/Proyectos/Reactor/core/database.py` — `consultar_runs_auto()` and `cargar_resultados_de_run()`
