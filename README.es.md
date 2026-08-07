# R.E.A.C.T.O.R

<!--toc:start-->
- [R.E.A.C.T.O.R](#reactor)
  - [Características](#características)
  - [Instalación](#instalación)
    - [AppImage (recomendado)](#appimage-recomendado)
    - [Desde fuente](#desde-fuente)
    - [Requisitos (modo fuente)](#requisitos-modo-fuente)
    - [Ejecutar tests](#ejecutar-tests)
  - [Estructura del proyecto](#estructura-del-proyecto)
  - [Sistema de Puntuación](#sistema-de-puntuación)
    - [1. Métricas Crudas](#1-métricas-crudas)
    - [2. Score por Categoría](#2-score-por-categoría)
    - [3. Puntaje Final](#3-puntaje-final)
    - [4. Ranking Manual](#4-ranking-manual)
  - [Licencia](#licencia)
  - [Traducciones](#traducciones)
<!--toc:end-->

[![License](https://img.shields.io/badge/license-GPLv3-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-blue)](https://www.kernel.org/)
[![Language](https://img.shields.io/badge/language-Python-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![GTK](https://img.shields.io/badge/gtk-4.0-47748F?logo=gnome&logoColor=white)](https://www.gtk.org/)
[![Status](https://img.shields.io/badge/status-v1.0.5%20Release-blue)](Status)
[![Build](https://img.shields.io/badge/build-AppImage-success)](Sucess)

Reactor de Experimentación Avanzada Concurrente Telúrico para Optimización de Rendimiento

Herramienta de benchmarking y gestión de schedulers `scx` en Linux.
Proporciona una interfaz gráfica GTK4/Libadwaita para:

- inspección de schedulers disponibles,
- ejecución de benchmarks de rendimiento,
- detección automática del mejor scheduler,
- historial y análisis de resultados,
- diagnóstico térmico y compatibilidad.

## Características

- UI moderna con GTK4 y Libadwaita.
  <img width="1000" height="900" alt="image" src="https://github.com/user-attachments/assets/5cbb2996-92bd-4b02-a112-72f7155bc23f" />

- Control de schedulers SCX mediante `scxctl`.
  <img width="1000" height="900" alt="image" src="https://github.com/user-attachments/assets/24fe4805-bedd-441c-a952-8099bd030df7" />

- **AppImage autocontenido** con hyperfine, cyclictest incluidos.
- Persistencia de historial en SQLite (`~/.local/share/scxctl/history.db`).
  <img width="1000" height="1057" alt="image" src="https://github.com/user-attachments/assets/479d50b5-35ef-4301-b23b-29056b28754a" />

- Comparación de resultados con ponderaciones ajustables (potencia, respuesta, fluidez).
  <img width="1013" height="1345" alt="image" src="https://github.com/user-attachments/assets/d1964867-bd58-47ca-886d-938abc9dcdc7" />

- Vista de logs técnicos y gráficos de tendencias con tabla comparativa nativa Gtk.ColumnView.
  <img width="934" height="990" alt="image" src="https://github.com/user-attachments/assets/03534ff5-6782-4182-a3f7-14eacd7403be" />

- Verificación de compatibilidad con kernel y componentes.
  <img width="934" height="990" alt="image" src="https://github.com/user-attachments/assets/f9a2e4b4-bb70-4144-b06e-0e14dc838158" />

- Diagnóstico en vivo: CPU, memoria, temperatura, planificador, eventos sched_ext.
  <img width="934" height="990" alt="image" src="https://github.com/user-attachments/assets/2a9be66b-c745-4a5b-a96d-c60287070541" />

- Terminal scxtop embebida para monitoreo avanzado.
  <img width="934" height="990" alt="image" src="https://github.com/user-attachments/assets/88ec473c-2749-4960-b952-060dc8086e59" />

- RadarChart de rendimiento para comparación visual rápida.
  <img width="459" height="316" alt="image" src="https://github.com/user-attachments/assets/65e099d7-cc7f-4c1c-8059-774ecb9cead8" />

- Internacionalización (i18n): español, inglés, francés, alemán, italiano, portugués.
  <img width="578" height="173" alt="image" src="https://github.com/user-attachments/assets/663b1265-6eef-44aa-ac3b-73e7f97b5876" />

- 57 tests automatizados (scoring, benchmark, database, thermal, hybrid).
  <img width="934" height="990" alt="image" src="https://github.com/user-attachments/assets/2c8845d7-c60f-4fb9-96df-a13c35cd6b46" />

- Documento de aprendizaje con patrones y buenas prácticas.
  <img width="583" height="513" alt="image" src="https://github.com/user-attachments/assets/56e4fe09-bbd8-42ab-b2a3-47be145aca75" />


## Instalación

### AppImage (recomendado)

Descarga el `R.E.A.C.T.O.R-*.AppImage` desde [Releases](https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R/releases),
se le realiza ejecutable y ejecución:

```bash
chmod +x R.E.A.C.T.O.R-*.AppImage
./R.E.A.C.T.O.R-*.AppImage
```

El AppImage incluye hyperfine y cyclictest —
no necesita instalación en el sistema para esos
(stress-ng se requiere en el sistema).

### Desde fuente

```bash
git clone https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R.git
cd R.E.A.C.T.O.R
pip install -e ".[test]"
python3 main.py
```

### Requisitos (modo fuente)

- Linux con Python >= 3.10
- GTK 4, Libadwaita >= 1
- `scxctl` (del sistema, específico del kernel)
- `sudo` o `run0` con sesión activa
- `stress-ng`, `hyperfine`, `gcc` + `make`
(para benchmark compile; stress-ng necesario incluso en AppImage)

### Ejecutar tests

```bash
pytest
```

## Estructura del proyecto

- `main.py` — punto de entrada, valida dependencias y arranca la aplicación.
- `app.py` — ventana principal y configuración global.
- `pyproject.toml` — configuración del proyecto, dependencias y herramientas.
- `core/` — lógica de negocio:
  - `scx.py` — interacción con `scxctl`.
  - `benchmark.py` — benchmarks con stress-ng.
  - `hybrid.py` — benchmarks de latencia con hyperfine.
  - `scoring.py` — ranking y scores con media armónica.
  - `database.py` — historial y compatibilidad en SQLite.
  - `verificacion.py` — verificación del sistema e integridad AppImage.
  - `thermal.py` — sensor térmico con caché inteligente.
  - `constantes.py` — constantes compartidas (VERSION, umbrales, intervalos).
  - `estado.py` — dataclasses de estado.
  - `tipos.py` — tipos de prueba y fórmulas de valor.
- `ui/` — interfaz de usuario:
  - `automatizacion/` — detección automática del mejor scheduler.
  - `historial/` — historial con tendencias y tabla comparativa.
  - `diagnostico/` — monitoreo en vivo y scxtop.
  - `controles.py` — control de schedulers.
  - `disponibilidad.py` — verificación BPF.
  - `rendimiento.py` — benchmarks manuales.
  - `grafico.py` — gráfico de barras animado con radar.
  - `info_pruebas.py` — contenido de ayuda de benchmarks.
  - `verificacion.py` — diálogo de verificación.
- `utils/` — utilidades:
  - `helpers.py` — logging, colores, lscpu, utilidades generales.
  - `iconos.py` — iconos portátiles + GResource.
  - `i18n.py` — internacionalización gettext.
- `widgets/` — componentes GTK reutilizables:
  - `radar.py` — RadarChart animado con 6 ejes.
  - `circular_meter.py` — medidor circular.
  - `legend.py` — chips de leyenda interactivos.
- `appimage/` — archivos de lanzamiento AppImage (AppRun, desktop, icono).
- `data/icons/` — 43 iconos SVG bundleados.
- `tests/` — 57 tests automatizados.
- `scripts/` — scripts de build y traducción.
- `po/` — archivos de traducción (.po/.mo).

## Sistema de Puntuación

El motor de scoring (`core/scoring.py`)
evalúa los planificadores en 6 categorías de benchmark
usando una fórmula ponderada multidimensional.

### 1. Métricas Crudas

Cada benchmark produce tres valores:

| Valor | Significado | Fuente |
| ------- | ------------- | -------- |
| `val` | Métrica principal | Throughput (stress-ng) o latencia (hyperfine) |
| `p95` | Variabilidad | Percentil 95 o desviación estándar |
| `waste` | Ineficiencia | `(100 - cpu_usage)/100` o CV de hyperfine |

### 2. Score por Categoría

Para cada tipo de test se calculan tres ratios
contra el mejor planificador en esa categoría:

```text
r_pot = my_val / best_val       (tipos throughput: cpu, threads, memory)
r_pot = best_val / my_val       (tipos latencia: fork, compile, loaded)
r_lat = best_p95 / my_p95       (siempre: menor variabilidad = mejor)
r_flu = max(0.01, 1.0 - waste)  (siempre: mayor uso de CPU = mejor)
```

Se combinan con pesos ajustables:

```text
cat_score = r_pot × P_pot + r_lat × P_lat + r_flu × P_flu
```

Pesos por defecto: **Potencia 45%**, **Respuesta 45%**, **Fluidez 10%**.

### 3. Puntaje Final

Todos los scores de categoría se combinan con **media armónica**:

```text
final = n / (1/s₁ + 1/s₂ + ... + 1/sₙ)
```

La media armónica penaliza valores bajos en cualquier categoría,
asegurando que se prefiera un planificador balanceado sobre uno
que sobresalga en una sola métrica.

El puntaje final se escala a porcentaje: `score = media_armónica × 100`.

### 4. Ranking Manual

Para benchmarks manuales (pestaña Rendimiento), se aplica la misma fórmula,
pero cada resultado se compara contra los demás resultados
del mismo tipo de test en la sesión actual.

## Licencia

Este proyecto está licenciado bajo la GNU General Public License v3.0.
Consulta el archivo [LICENSE](LICENSE) para más detalles.

## Traducciones

- [English](README.md)
