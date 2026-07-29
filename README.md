# R.E.A.C.T.O.R

**Reactor Experimental Avanzado Concurrente Telúrico para Optimización de Rendimiento**

[![License](https://img.shields.io/badge/license-GPLv3-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-blue)](https://www.kernel.org/)
[![Language](https://img.shields.io/badge/language-Python-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![GTK](https://img.shields.io/badge/gtk-4.0-47748F?logo=gnome&logoColor=white)](https://www.gtk.org/)
[![Status](https://img.shields.io/badge/status-v1.2%20Stable-brightgreen)]()

A CPU scheduler benchmarking and optimization tool for Linux `scx` schedulers. Provides a GTK4/Libadwaita GUI for:
- inspecting available schedulers,
- running performance benchmarks,
- automatic detection of the best scheduler,
- history and results analysis,
- thermal and compatibility diagnostics.

## Features

- Modern UI with GTK4 and Libadwaita.
- SCX scheduler control via `scxctl`.
- History persistence in SQLite (`~/.local/share/scxctl/history.db`).
- Result comparison with adjustable weights (power, response, smoothness).
- Technical logs and trend charts with native Gtk.ColumnView.
- Kernel and component compatibility verification.
- Live diagnostics: CPU, memory, temperature, scheduler, sched_ext events.
- Embedded scxtop terminal for advanced monitoring.
- Performance radar chart for quick visual comparison.
- Internationalization (i18n) with gettext: Spanish, English, French, German, Italian, Portuguese.
- Info panels for each benchmark test explaining methodology and interpretation.
- 49 automated tests (scoring, benchmark, database, thermal).

## Tested Distributions

| Distro | Status |
|---|---|
| Solus 4.9+ | ![Working](https://img.shields.io/badge/-Working-brightgreen) |
| Arch Linux | ![Pending](https://img.shields.io/badge/-Pending-lightgrey) |
| Fedora | ![Pending](https://img.shields.io/badge/-Pending-lightgrey) |
| openSUSE | ![Pending](https://img.shields.io/badge/-Pending-lightgrey) |
| Debian | ![Pending](https://img.shields.io/badge/-Pending-lightgrey) |
| Ubuntu | ![Pending](https://img.shields.io/badge/-Pending-lightgrey) |
| NixOS | ![Pending](https://img.shields.io/badge/-Pending-lightgrey) |

## Requirements

- Linux
- Python >= 3.10
- GTK 4
- Libadwaita >= 1
- `scxctl`
- `sudo` or `run0` with active session for system operations
- `stress-ng`
- `hyperfine`

## Installation

```bash
git clone https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R.git
cd R.E.A.C.T.O.R
pip install -e ".[test]"
```

Or run directly without installing:

```bash
python3 main.py
```

### Running tests

```bash
pytest
```

## Project Structure

- `main.py` — entry point, validates dependencies and starts the application.
- `app.py` — main window and global application setup.
- `pyproject.toml` — project configuration, dependencies, and tools.
- `core/` — business logic:
  - `scx.py` — interaction with `scxctl`.
  - `benchmark.py` — benchmark execution with stress-ng.
  - `hybrid.py` — latency benchmarks with hyperfine.
  - `scoring.py` — ranking and score calculation with harmonic mean.
  - `database.py` — history and compatibility storage in SQLite.
  - `thermal.py` — thermal sensor with intelligent caching.
  - `constantes.py` — shared constants (version, thresholds, intervals).
  - `estado.py` — state dataclasses (test state, monitor state, auto-detection state).
- `ui/` — user interface:
  - `automatizacion/` — automatic scheduler detection:
    - `__init__.py` — setup and public re-exports.
    - `pesos.py` — weight sliders with presets and live ranking.
    - `historial.py` — auto-run navigation.
    - `deteccion.py` — detection engine and winner application.
  - `historial/` — results history:
    - `pagina.py` — page with InLineViewSwitcher (results / trend / environment).
    - `constantes.py` — test types and date ranges.
    - `resultados.py` — result list with chips, filters, and refresh.
    - `tendencia.py` — Cairo chart + sortable Gtk.ColumnView table.
    - `dibujo.py` — Cairo rendering, hover tooltip with crosshair, fade-out.
    - `entorno.py` — system info, hardware, and radar chart.
  - `diagnostico/` — live diagnostics:
    - `pagina.py` — page with InLineViewSwitcher (monitor / scxtop).
    - `monitoreo.py` — live monitor for CPU, memory, temperature, scheduler, events.
    - `scxtop.py` — embedded VTE terminal for scxtop.
  - `controles.py` — scheduler control (selection, apply, stop).
  - `disponibilidad.py` — BPF compatibility verification.
   - `rendimiento.py` — manual benchmark execution.
   - `info_pruebas.py` — benchmark info content for help bottom sheets.
   - `grafico.py` — animated bar chart with integrated radar.
- `utils/` — utilities:
  - `helpers.py` — lscpu parsers, color generation, logging.
  - `iconos.py` — portable icon constants + GResource registration.
  - `i18n.py` — internationalization (gettext .po/.mo support).
- `widgets/` — reusable GTK components:
  - `radar.py` — animated radar chart with 6 axes.
  - `circular_meter.py` — circular meter for CPU/memory/temperature.
  - `legend.py` — interactive legend chips.
- `data/icons/` — bundled SVG icons (43 Adwaita + icon-development-kit icons).
- `tests/` — automated tests:
  - `test_scoring.py` — scoring engine, harmonic mean, normalization.
  - `test_benchmark.py` — stress-ng YAML parser.
  - `test_database.py` — SQLite operations with temp database.
  - `test_thermal.py` — thermal sensor with filesystem mock.
- `po/` — translation files (.po/.mo) for internationalization.
- `design/` — design assets.
- `scripts/` — utility scripts (translation extraction, build, etc.).

## License

This project is licensed under the GNU General Public License v3.0. See the [LICENSE](LICENSE) file for details.

## Translations

- [Español](README.es.md)
