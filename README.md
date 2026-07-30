# R.E.A.C.T.O.R

**Reactor Experimental Avanzado Concurrente Telúrico para Optimización de Rendimiento**

[![License](https://img.shields.io/badge/license-GPLv3-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-blue)](https://www.kernel.org/)
[![Language](https://img.shields.io/badge/language-Python-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![GTK](https://img.shields.io/badge/gtk-4.0-47748F?logo=gnome&logoColor=white)](https://www.gtk.org/)
[![Status](https://img.shields.io/badge/status-v1.0.5%20Release-blue)]()
[![Build](https://img.shields.io/badge/build-AppImage-success)]()

A CPU scheduler benchmarking and optimization tool for Linux `scx` schedulers. Provides a GTK4/Libadwaita GUI for:
- inspecting available schedulers,
- running performance benchmarks,
- automatic detection of the best scheduler,
- history and results analysis,
- thermal and compatibility diagnostics.

## Features

- Modern UI with GTK4 and Libadwaita.
- SCX scheduler control via `scxctl`.
- **Self-contained AppImage** with hyperfine, cyclictest bundled.
- History persistence in SQLite (`~/.local/share/scxctl/history.db`).
- Result comparison with adjustable weights (power, response, smoothness).
- Technical logs and trend charts with native Gtk.ColumnView.
- Kernel and component compatibility verification.
- Live diagnostics: CPU, memory, temperature, scheduler, sched_ext events.
- Embedded scxtop terminal for advanced monitoring.
- Performance radar chart for quick visual comparison.
- Internationalization (i18n) with gettext: Spanish, English, French, German, Italian, Portuguese.
- Info panels for each benchmark test explaining methodology and interpretation.
- 57 automated tests (scoring, benchmark, database, thermal, hybrid).
- Learning document with architecture patterns and best practices.

## Installation

### AppImage (recommended)

Download the latest `R.E.A.C.T.O.R-*.AppImage` from the [Releases](https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R/releases) page, make it executable and run:

```bash
chmod +x R.E.A.C.T.O.R-*.AppImage
./R.E.A.C.T.O.R-*.AppImage
```

The AppImage bundles hyperfine and cyclictest — no system installation needed for those (stress-ng is required on the system).

### From source

```bash
git clone https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R.git
cd R.E.A.C.T.O.R
pip install -e ".[test]"
python3 main.py
```

### Requirements (source mode)

- Linux with Python >= 3.10
- GTK 4, Libadwaita >= 1
- `scxctl` (from system, kernel-specific)
- `sudo` or `run0` with active session
- `stress-ng`, `hyperfine`, `gcc` + `make` (for compile benchmark; stress-ng needed even for AppImage)

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
  - `verificacion.py` — system and AppImage integrity verification.
  - `thermal.py` — thermal sensor with intelligent caching.
  - `constantes.py` — shared constants (version, thresholds, intervals).
  - `estado.py` — state dataclasses (test state, monitor state, auto-detection state).
  - `tipos.py` — test types, chart mapping, value formulas.
- `ui/` — user interface:
  - `automatizacion/` — automatic scheduler detection.
  - `historial/` — results history with trend chart and ColumnView.
  - `diagnostico/` — live diagnostics with monitor and scxtop.
  - `controles.py` — scheduler control (selection, apply, stop).
  - `disponibilidad.py` — BPF compatibility verification.
  - `rendimiento.py` — manual benchmark execution.
  - `grafico.py` — animated bar chart with integrated radar.
  - `info_pruebas.py` — benchmark info content for help bottom sheets.
  - `verificacion.py` — verification dialog (Adw.Dialog).
- `utils/` — utilities:
  - `helpers.py` — lscpu parsers, color generation, logging, general-purpose helpers.
  - `iconos.py` — portable icon constants + GResource registration.
  - `i18n.py` — internationalization (gettext .po/.mo support).
- `widgets/` — reusable GTK components:
  - `radar.py` — animated radar chart with 6 axes.
  - `circular_meter.py` — circular meter for CPU/memory/temperature.
  - `legend.py` — interactive legend chips with dot + toggle.
- `appimage/` — AppImage launcher files (AppRun, desktop, icon).
- `data/icons/` — bundled SVG icons (43 Adwaita + icon-development-kit icons).
- `tests/` — automated tests (57 total):
  - `test_scoring.py` — scoring engine, harmonic mean, normalization, `calcular_score_categorias`.
  - `test_benchmark.py` — stress-ng YAML parser.
  - `test_database.py` — SQLite operations with temp database.
  - `test_thermal.py` — thermal sensor with filesystem mock.
  - `test_hybrid.py` — hyperfine helpers and conversions.
- `scripts/` — utility scripts (`build-appimage.sh`, translation extraction).
- `po/` — translation files (.po/.mo) for internationalization.

## Scoring System

The scoring engine (`core/scoring.py`) evaluates schedulers across 6 benchmark categories using a weighted, multi-dimensional formula.

### 1. Raw Metrics

Each benchmark produces three raw values:

| Value | Meaning | Source |
|-------|---------|--------|
| `val` | Primary metric | Throughput (stress-ng) or latency (hyperfine) |
| `p95` | Variability | 95th percentile or std deviation |
| `waste` | Inefficiency | `(100 - cpu_usage)/100` or CV from hyperfine |

### 2. Per-Category Score

For each test type, three ratios are computed against the best scheduler in that category:

```
r_pot = my_val / best_val       (throughput types: cpu, threads, memory)
r_pot = best_val / my_val       (latency types: fork, compile, loaded)
r_lat = best_p95 / my_p95       (always: lower variability = better)
r_flu = max(0.01, 1.0 - waste)  (always: higher CPU usage = better)
```

These are combined with user-adjustable weights:

```
cat_score = r_pot × W_pot + r_lat × W_lat + r_flu × W_flu
```

Default weights: **Potency 45%**, **Response 45%**, **Fluidity 10%**.

### 3. Final Score

All category scores are combined using the **harmonic mean**:

```
final = n / (1/s₁ + 1/s₂ + ... + 1/sₙ)
```

The harmonic mean penalizes low scores in any single category, ensuring a balanced scheduler is preferred over one that excels only in one metric.

Final score is scaled to a percentage: `score = harmonic_mean × 100`.

### 4. Manual Ranking

For manual benchmarks (Rendimiento tab), the same formula applies, but each result is compared against all other results of the same test type in the current session.

---

## License

This project is licensed under the GNU General Public License v3.0. See the [LICENSE](LICENSE) file for details.

## Translations

- [Español](README.es.md)
