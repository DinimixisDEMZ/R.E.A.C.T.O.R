# R.E.A.C.T.O.R

[![License](https://img.shields.io/badge/license-GPLv3-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-blue)](https://www.kernel.org/)
[![Language](https://img.shields.io/badge/language-Python-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![GTK](https://img.shields.io/badge/gtk-4.0-47748F?logo=gnome&logoColor=white)](https://www.gtk.org/)
[![Status](https://img.shields.io/badge/status-v1.0%20Stable-brightgreen)]()

Reactor de Experimentación Avanzada Concurrente Telúrico para Optimización de Rendimiento

Herramienta de benchmarking y gestión de schedulers `scx` en Linux. Proporciona una interfaz gráfica GTK4/Libadwaita para:
- inspección de schedulers disponibles,
- ejecución de benchmarks de rendimiento,
- detección automática del mejor scheduler,
- historial y análisis de resultados,
- diagnóstico térmico y compatibilidad.

## Características

- UI moderna con GTK4 y Libadwaita.
- Control de schedulers SCX mediante `scxctl`.
- Persistencia de historial en SQLite (`~/.local/share/scxctl/history.db`).
- Comparación de resultados con ponderaciones ajustables (potencia, respuesta, fluidez).
- Vista de logs técnicos y gráficos de tendencias con tabla comparativa nativa Gtk.ColumnView.
- Verificación de compatibilidad con kernel y componentes.
- Diagnóstico en vivo: CPU, memoria, temperatura, planificador, eventos sched_ext.
- Terminal scxtop embebida para monitoreo avanzado.
- RadarChart de rendimiento para comparación visual rápida.
- 49 tests automatizados (scoring, benchmark, database, thermal).

## Distribuciones probadas

| Distro | Estado |
|---|---|
| Solus 4.9+ | ![Funciona](https://img.shields.io/badge/-Funciona-brightgreen) |
| Arch Linux | ![Pendiente](https://img.shields.io/badge/-Pendiente-lightgrey) |
| Fedora | ![Pendiente](https://img.shields.io/badge/-Pendiente-lightgrey) |
| openSUSE | ![Pendiente](https://img.shields.io/badge/-Pendiente-lightgrey) |
| Debian | ![Pendiente](https://img.shields.io/badge/-Pendiente-lightgrey) |
| Ubuntu | ![Pendiente](https://img.shields.io/badge/-Pendiente-lightgrey) |
| NixOS | ![Pendiente](https://img.shields.io/badge/-Pendiente-lightgrey) |

## Requisitos

- Linux
- Python >= 3.10
- GTK 4
- Libadwaita 1
- `scxctl`
- `sudo` o `run0` con sesión activa para operaciones de sistema
- `stress-ng`
- `hyperfine`

## Instalación

```bash
git clone https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R.git
cd R.E.A.C.T.O.R
pip install -e ".[test]"
```

O ejecuta directamente sin instalar:

```bash
python3 main.py
```

## Ejecutar tests

```bash
pytest
```

## Estructura del proyecto

- `main.py` — punto de entrada, valida dependencias y arranca la aplicación.
- `app.py` — ventana principal y configuración global de la aplicación.
- `pyproject.toml` — configuración del proyecto, dependencias y herramientas.
- `core/` — lógica de negocio:
  - `scx.py` — interacción con `scxctl`.
  - `benchmark.py` — ejecución de benchmarks con stress-ng.
  - `hybrid.py` — benchmarks de latencia con hyperfine.
  - `scoring.py` — cálculo de ranking y scores con media armónica.
  - `database.py` — almacenamiento de historial y compatibilidad en SQLite.
  - `thermal.py` — sensor térmico con caché inteligente.
  - `constantes.py` — constantes compartidas (VERSION, umbrales, intervalos).
  - `estado.py` — dataclasses de estado (BenchmarkState, MonitorState, AutoDetectionState).
- `ui/` — interfaz de usuario:
  - `automatizacion/` — detección automática del mejor scheduler:
    - `__init__.py` — setup y re-exports públicos.
    - `pesos.py` — sliders de peso con presets y ranking en vivo.
    - `historial.py` — navegación de runs automáticos.
    - `deteccion.py` — motor de detección y aplicación del ganador.
  - `historial/` — historial de resultados:
    - `pagina.py` — página con InLineViewSwitcher (Resultados / Tendencia / Entorno).
    - `constantes.py` — tipos de prueba y rangos de fecha.
    - `resultados.py` — lista de resultados con chips, filtros y refresh.
    - `tendencia.py` — gráfico Cairo + tabla comparativa Gtk.ColumnView sortable.
    - `dibujo.py` — renderizado Cairo, hover tooltip con crosshair, fade-out.
    - `entorno.py` — info del sistema, hardware y RadarChart.
  - `diagnostico/` — diagnóstico en vivo:
    - `pagina.py` — página con InLineViewSwitcher (Monitor / scxtop).
    - `monitoreo.py` — monitor en vivo CPU, memoria, temperatura, planificador, eventos.
    - `scxtop.py` — terminal VTE embebida para scxtop.
  - `controles.py` — control de schedulers (selección, apply, stop).
  - `disponibilidad.py` — verificación de compatibilidad BPF.
  - `rendimiento.py` — ejecución manual de benchmarks.
  - `grafico.py` — gráfico de barras animado con radar integrado.
- `utils/` — utilidades:
  - `helpers.py` — parsers de lscpu, generación de color, logging.
  - `iconos.py` — constantes de iconos portátiles + registro de GResource.
- `widgets/` — componentes GTK reutilizables:
  - `radar.py` — RadarChart animado con 6 ejes.
  - `circular_meter.py` — medidor circular para CPU/memoria/temperatura.
  - `legend.py` — chips de leyenda interactivos.
- `data/icons/` — iconos SVG bundleados (43 iconos de Adwaita + icon-development-kit).
- `tests/` — tests automatizados:
  - `test_scoring.py` — scoring engine, media armónica, normalización.
  - `test_benchmark.py` — parser YAML de stress-ng.
  - `test_database.py` — operaciones SQLite con DB temporal.
  - `test_thermal.py` — sensor térmico con filesystem mock.
- `design/` — assets de diseño.

## Licencia

Este proyecto está licenciado bajo la GNU General Public License v3.0. Consulta el archivo [LICENSE](LICENSE) para más detalles.

## Traducciones

- [English](README.md)
