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
- **`.fine-tune` removed** — no more thickness change on touch
- **Tighter spacing** — zero margins/spacing between slider rows, native Adw.PreferencesGroup handles layout
- **Header icon presets** — moved preset buttons from below listbox to header as circular icon buttons: `object-select-symbolic` (Balanceado), `power-profile-performance-symbolic` (Potencia), `preferences-system-time-symbolic` (Respuesta), `weather-windy-symbolic` (Fluidez); info button with tooltip
- **Header outside group** — `Adw.ActionRow(title="Ajustar Pesos", activatable=False)` added directly to `pref_page` (not inside `Adw.PreferencesGroup`), with buttons as `add_suffix()`; avoids row borders
- **No more `Gtk.ListBox` for sliders** — sliders sit in a plain `Gtk.Box` inside `win.revealer_lista`; no separators or boxed-list borders
- **Removed `fila_espera` placeholder** — listbox replaced by revealer that toggles visibility
- **Recalc bar below sliders** — wrapped in `win.revealer_recalc` (SLIDE_DOWN) for slide-in/out animation; 50px bar right-aligned
- **Name labels removed from slider rows** — icons alone identify Potencia / Respuesta / Fluidez
- **`_lbl_pot/_resp/_flu` cleaned up** — no more dead `Gtk.Label` references or `set_label` calls
- **Checkbox scheduler selection** for auto-detection — `_refrescar_auto_schedulers(win)` populates checkable `Adw.ActionRow` list in `grupo_auto`
- **DB threading fix** — `activar_db_temporal()` uses `check_same_thread=False`
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
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/core/scoring.py` — `calcular_scores_finales()` accepts `pesos` param
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/core/benchmark.py` — p95 reads `nanosecs-per-mutex` for memory type
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/ui/automatizacion.py` — scheduler checklist, weight sliders with presets, history nav
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/ui/disponibilidad.py` — `recargar_disponibilidad_ui` non-destructive refresh
- `/home/dinimixis/Documentos/Proyectos/R.E.A.C.T.O.R/core/database.py` — `consultar_runs_auto()` and `cargar_resultados_de_run()`
