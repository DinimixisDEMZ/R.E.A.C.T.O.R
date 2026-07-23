# R.E.A.C.T.O.R

[![License](https://img.shields.io/badge/license-Fair%20Source-orange)](https://faircode.io)
[![Platform](https://img.shields.io/badge/platform-Linux-blue)](https://www.kernel.org/)
[![Language](https://img.shields.io/badge/language-Python-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![GTK](https://img.shields.io/badge/gtk-4.0-47748F?logo=gnome&logoColor=white)](https://www.gtk.org/)
[![Status](https://img.shields.io/badge/status-Experimental-yellow)]()

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
- Comparación de resultados con ponderaciones ajustables.
- Vista de logs técnicos y gráficos de tendencias con tabla comparativa nativa.
- Verificación de compatibilidad con kernel y componentes.
- Diagnóstico en vivo: CPU, memoria, temperatura, planificador, eventos sched_ext.
- Terminal scxtop embebida para monitoreo avanzado.
- RadarChart de rendimiento para comparación visual rápida.

## Requisitos

- Linux
- Python 3.11+ (o Python 3 moderno compatible)
- GTK 4
- Libadwaita 1
- `scxctl`
- `sudo` con sesión activa para operaciones de sistema
- `stress-ng`
- `hyperfine`

## Estructura del proyecto

- `main.py` - punto de entrada, valida dependencias y arranca la aplicación.
- `app.py` - ventana principal y configuración global de la aplicación.
- `core/` - lógica de negocio:
  - `scx.py` - interacción con `scxctl`.
  - `benchmark.py` - ejecución de benchmarks.
  - `scoring.py` - cálculo de ranking y scores.
  - `database.py` - almacenamiento de historial y compatibilidad.
  - `thermal.py` - monitor térmico.
  - `hybrid.py` - lógica de benchmarks híbridos.
- `ui/historial/` - historial de resultados:
  - `pagina.py` - página con InLineViewSwitcher (Resultados / Tendencia / Entorno).
  - `constantes.py` - tipos de prueba y rangos de fecha.
  - `resultados.py` - lista de resultados con chips, filtros y refresh.
  - `tendencia.py` - gráfico Cairo + tabla comparativa Gtk.ColumnView sortable.
  - `dibujo.py` - renderizado Cairo, hover tooltip con crosshair, fade-out.
  - `entorno.py` - info del sistema, hardware y RadarChart.
- `ui/diagnostico/` - diagnóstico en vivo:
  - `pagina.py` - página con InLineViewSwitcher (Monitor / scxtop).
  - `monitoreo.py` - monitor en vivo CPU, memoria, temperatura, planificador, eventos.
  - `scxtop.py` - terminal VTE embebida para scxtop.
- `ui/` - demás pestañas (rendimiento, automatización, disponibilidad, controles, etc.)
- `utils/` - utilidades de ayuda (`helpers.py` con parsers de lscpu, generación de color, etc.).
- `widgets/` - componentes GTK reutilizables (`radar.py` con RadarChart).
- `design/` - assets de diseño (`vte_colors.py`, etc.).

## Uso

1. Asegúrate de tener dependencias instaladas en tu sistema.
2. Abre una terminal en el directorio del proyecto.
3. Ejecuta:

```bash
python3 main.py
```

Si no tienes una sesión de sudo activa, la aplicación te solicitará autenticación para ejecutar comandos de sistema.

## Base de datos de historial

La aplicación guarda metadata y resultados en una base de datos SQLite ubicada en:

```text
~/.local/share/scxctl/history.db
```

## Nota

La aplicación está diseñada específicamente para Linux y requiere que `scxctl` esté disponible en el sistema. Si `scxctl` no se encuentra, `main.py` muestra un error y detiene el arranque.

## Licencia

Este proyecto se distribuye bajo la Fair Source License 2.0.

La Fair Source License es una licencia de código fuente disponible (`source-available`) pero no es una licencia de software libre u open source aprobada por OSI. Esto significa que:

- el código fuente puede leerse, modificarse y distribuirse para uso interno,
- el uso por cinco (5) o más usuarios, o el uso para ofrecer el software como servicio a terceros, requiere una licencia comercial,
- el titular del copyright mantiene la propiedad del código,
- las condiciones completas se encuentran en el archivo `LICENSE`.

Para más información: https://faircode.io.

Si eres un usuario curioso, esto quiere decir que puedes inspeccionar el proyecto y probarlo, pero el uso comercial o multiusuario está limitado según los términos de la licencia.
