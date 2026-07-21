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
- Vista de logs técnicos y gráficos de tendencias.
- Verificación de compatibilidad con kernel y componentes.

## Requisitos

- Linux (comprobación crítica durante el preflight)
- Python 3.11+ (o Python 3 moderno compatible)
- GTK 4, Libadwaita 1.7+ y PyGObject (comprobación crítica durante el preflight; `Adw.WrapBox` lo requiere)
- `scxctl` (comprobación crítica durante el preflight)
- Para controles y automatización privilegiados: ejecución directa como root, `run0` o `sudo`. Si no hay ninguno disponible, el preflight avisa pero permite abrir la interfaz.
- `stress-ng` para benchmarks de estrés; solo expone respuesta agregada cuando existe una métrica independiente de context switch o mutex, nunca un p95 real.
- `hyperfine` para benchmarks de latencia; calcula el p95 real a partir de un mínimo de 20 muestras en las pruebas `compile` y `loaded`.
- Un compilador C disponible como `cc`, `gcc` o `clang` para `compile`. Reactor resuelve y valida una ruta regular y ejecutable, genera una carga C determinista dentro de un directorio temporal privado y no depende de `/tmp/rt-tests` ni de un árbol fuente preexistente.

## Preflight

Antes de importar la interfaz, `main.py` comprueba Linux, `scxctl`, GTK/PyGObject y que Libadwaita sea >= 1.7, requisito de `Adw.WrapBox`. Los errores críticos se muestran en un diálogo gráfico cuando GTK está disponible; si PyGObject, GTK o una versión compatible de Libadwaita faltan, se imprime un diagnóstico claro por `stderr`. El preflight también detecta si el proceso ya se ejecuta como root y busca los backends `sudo` y `run0`. Si no existe ninguna vía de elevación, la aplicación se abre y emite un aviso explícito de que los controles y la automatización privilegiados no estarán disponibles. La ausencia de `stress-ng`, `hyperfine` o de los nombres `cc`/`gcc`/`clang` mantiene un aviso específico para el benchmark afectado y tampoco bloquea el arranque de la interfaz. Al iniciar `compile`, el motor vuelve a resolver el compilador y valida que su ruta real sea regular y ejecutable; si no es segura, solo esa medición se rechaza con un error explícito.

## Estructura del proyecto

- `main.py` - punto de entrada, valida dependencias y arranca la aplicación.
- `app.py` - ventana principal y configuración global de la aplicación.
- `core/` - lógica de negocio:
  - `scx.py` - interacción con `scxctl` y ejecución privilegiada mediante el backend autodetectado (`sudo` o `run0`).
  - `benchmark.py` - ejecución de benchmarks.
  - `scoring.py` - cálculo de ranking y scores.
  - `database.py` - almacenamiento de historial y compatibilidad.
  - `thermal.py` - monitor térmico.
  - `hybrid.py` - lógica de benchmarks híbridos.
- `ui/` - pestañas y componentes visuales.
- `utils/` - utilidades de ayuda.
- `widgets/` - componentes GTK reutilizables.

## Uso

1. Asegúrate de tener dependencias instaladas en tu sistema.
2. Abre una terminal en el directorio del proyecto.
3. Ejecuta:

```bash
python3 main.py
```

La aplicación autodetecta el backend privilegiado entre `run0` y `sudo` (o usa ejecución directa si ya se ejecuta como root). Cuando se usa `sudo`, puede solicitar autenticación; `run0` delega la autorización a su agente del sistema.

## Base de datos de historial

La aplicación guarda metadata y resultados en una base de datos SQLite ubicada en:

```text
~/.local/share/scxctl/history.db
```

## Nota

La aplicación está diseñada específicamente para Linux y requiere que `scxctl` esté disponible en el sistema. Si `scxctl` no se encuentra, `main.py` muestra un error y detiene el arranque.

Las recomendaciones y rankings de schedulers son experimentales: sirven para comparar ejecuciones en el entorno actual y no constituyen una garantía de rendimiento o estabilidad. Las pruebas con `stress-ng` solo exponen una respuesta agregada cuando existe una métrica independiente: tiempo medio de context switch (`mean_context_switch_us`) o de mutex (`mean_mutex_us`). La carga mixta (`threads`) no fabrica `response` a partir del throughput y se puntúa con throughput y fluidez; `p95` queda vacío y el p95 real se limita a las muestras de `hyperfine`.

Cada benchmark real captura un `ScxState` estricto al inicio y al final. Si el scheduler o su modo cambian durante la medición, Reactor descarta el resultado y registra ambos estados para impedir que se persista bajo una atribución incorrecta. En modo desarrollador se reutiliza un estado simulado estable.

Los artefactos YAML/JSON y la carga C generada viven en directorios temporales privados que se eliminan tras éxito, error, timeout o cancelación. En `loaded`, el proceso `stress-ng` conserva el cierre normal TERM/KILL del grupo creado por Reactor y además recibe un `--timeout` nativo, derivado del presupuesto y limitado, para que no sobreviva durante horas si Reactor termina abruptamente. La salida capturada de herramientas externas se limita y se entrega al log en bloques, evitando decenas de callbacks de interfaz por medición.

Las sesiones de prueba restauran el estado inicial del scheduler al terminar, también al cancelar o fallar una operación cuando la restauración está disponible. El análisis no aplica por sí solo una recomendación: cambiar el scheduler requiere una confirmación explícita en la interfaz.

## Licencia

Este proyecto se distribuye bajo la Fair Source License 2.0.

La Fair Source License es una licencia de código fuente disponible (`source-available`) pero no es una licencia de software libre u open source aprobada por OSI. Esto significa que:

- el código fuente puede leerse, modificarse y distribuirse para uso interno,
- el uso por cinco (5) o más usuarios, o el uso para ofrecer el software como servicio a terceros, requiere una licencia comercial,
- el titular del copyright mantiene la propiedad del código,
- las condiciones completas se encuentran en el archivo `LICENSE`.

Para más información: https://faircode.io.

Si eres un usuario curioso, esto quiere decir que puedes inspeccionar el proyecto y probarlo, pero el uso comercial o multiusuario está limitado según los términos de la licencia.
