# Bitácora de Desarrollo — R.E.A.C.T.O.R

## 2026-09-06 — Modernización al ecosistema scx 2026 y adaptación al hardware

### Objetivo
Revivir el proyecto, que llevaba meses pausado, y hacer que funcione en
cualquier PC de escritorio o portátil, vieja o nueva.

### El diagnóstico
El proyecto no estaba roto: se rompió el suelo bajo sus pies. `scxctl` y
`scx_loader` salieron del paquete de planificadores (en Arch pasaron a
`scx-tools`, y upstream a su propio repositorio `sched-ext/scx-loader`), y
`main.py` marcaba `scxctl` como dependencia crítica. En un equipo con el kernel
preparado y dieciséis planificadores instalados, la aplicación mostraba una
pantalla de dependencias faltantes y salía con código 1.

Además, `scx_loader` es hoy un servicio D-Bus del sistema autorizado por
polkit. El diálogo propio que pedía la contraseña y la canalizaba a `sudo -S`
había quedado obsoleto además de ser mala práctica.

### Lo que se hizo
- **Arranque**: sólo Linux con GTK es crítico. Todo lo demás degrada y se
  explica en una barra de avisos dentro de la aplicación.
- **Control**: `core/scx.py` reescrito con backends intercambiables (D-Bus,
  scxctl, directo con systemd-run, simulado). Se eliminó
  `widgets/password_dialog.py`.
- **Observación**: `core/sched_ext.py` nuevo. Lee el estado real del kernel sin
  privilegios y sin `scxctl`.
- **Hardware**: `core/hardware.py` y `core/dimensionado.py` nuevos. Topología,
  híbridos P/E, energía, chasis, y parámetros de prueba derivados del equipo.
- **Térmica**: `core/thermal.py` reescrito. Umbrales derivados del Tjmax que
  publica el hardware.
- **Catálogo**: los 16 planificadores de scx 1.1.3, con descripciones
  verificadas contra el `--help` de cada binario.
- **Pruebas**: de 57 a 240.

### Problemas encontrados
1. **La lista de prioridades del sensor térmico era decorativa.** El bucle
   recorría las zonas en orden y devolvía la primera que coincidiera con
   *cualquier* entrada de la lista, así que ganaba `acpitz` (zona 0) teniendo
   `x86_pkg_temp` disponible. Y el test existente afirmaba ese comportamiento
   como correcto: codificaba el bug.
2. **Mi propia corrección repitió el error que estaba corrigiendo.** El filtro
   de sensores excluía por subcadena, y `"ac"` coincide dentro de
   `"P·ac·kage id 0"`: descartaba justo el mejor sensor. Se resolvió con
   límites de palabra. Es exactamente el fallo que tenía el parser de `lscpu`.
3. **`lscpu` traduce sus nombres de campo.** En español devuelve "Nombre del
   modelo" en vez de "Model name". El parser no encontraba nada y su búsqueda
   por subcadena enganchaba campos equivocados: en francés la pestaña de
   entorno mostraba "66 CPUs" a "66 MHz" sin dar ningún error.
4. **Las dos vías obvias para detectar núcleos híbridos no funcionan en x86.**
   `topology/core_type` no existe y `cpu_capacity` vale 1024 en todos los
   núcleos. Lo que sí funciona es `/sys/devices/cpu_core/cpus` y
   `/sys/devices/cpu_atom/cpus`, que registra el subsistema perf.
5. **Un ratón inalámbrico se declara `type=Battery`.** Sin filtrar por `scope`,
   un equipo de escritorio con ratón Bluetooth pasaba por portátil.
6. **`pip install .` fallaba por completo.** Con `license` como expresión
   PEP 639, el clasificador de licencia dejó de ser un aviso y pasó a ser un
   error. Y `main.py` no entraba en el wheel, así que el punto de entrada
   `reactor` se instalaba sin el módulo que importa.
7. **Un import ausente que ninguna prueba veía.** `core/benchmark.py` usaba
   `obtener_dimensionado()` sin importarlo. La aplicación arrancaba con
   normalidad; habría fallado al lanzar el primer benchmark. Lo encontró `ruff`
   con `F821`, y por eso ahora esa comprobación es una prueba más.

### Aprendizajes
- Cuando una aplicación deja de funcionar sin que nadie la toque, el sospechoso
  es el entorno, no el código. Aquí el repositorio parecía correcto.
- Un test puede fijar un bug en su sitio. `test_priority_zone_detection`
  afirmaba que ganaba `acpitz` y protegía el fallo de cualquier corrección.
- Las comparaciones por subcadena en datos del sistema son una fuente
  reincidente de errores silenciosos: enganchan lo que no deben y fallan sin
  avisar.
- El análisis estático encuentra lo que las pruebas unitarias no pueden ver:
  código que no se ejecuta durante las pruebas.
- Nada que se pueda leer de `/sys` debería leerse de la salida de una
  herramienta que se traduce.

### Pendiente
- El "score de potencia" no mide energía: se deriva del uso de CPU que reporta
  stress-ng. O se renombra a lo que mide, o se implementa medición real (RAPL,
  `power_supply`) con degradación donde no exista.
- Las mediciones no fijan el gobernador de frecuencia. Hoy se registra en el
  historial, pero domina el resultado por encima del propio planificador.
- Traducciones de alemán, francés, italiano y portugués (36 de 365 cadenas).

---

## 2026-07-22 — Refactor masivo y ColumnView

### Objetivo
Modernizar la interfaz y reorganizar el proyecto en módulos con nombres en español.

### Logros
- Dividí `historial.py` y `diagnostico.py` en subdirectorios modulares (`ui/historial/`, `ui/diagnostico/`)
- Extraje `RadarChart` a `widgets/radar.py` (compartido entre Monitor y Entorno)
- Traduje ~200+ nombres de funciones, variables y constantes a español
- Reemplacé tabla comparativa (Gtk.Grid con botones) por `Gtk.ColumnView` nativo

### Problemas encontrados
1. **Sort del ColumnView no funcionaba**: `Gtk.CustomSorter.new()` con lambda se rompía porque PyGObject pasa `*user_data` como tercer argumento. Solución: usar `Gtk.CustomSorter()` + `set_sort_func()` con `*args` en el callback.
2. **Click en header no ordenaba**: faltaba el pipeline `ListStore → SortListModel(ColumnView.sorter) → MultiSelection`.
3. **Lógica de "mejor"**: cambié de flags pre-computados en cada fila a cálculo dinámico en `_on_bind`.

### Pendiente
- Rediseñar la tabla comparativa (bug de botones que crecen al hacer sort) → resuelto con ColumnView
- Centrar chips verticalmente → resuelto

---

## 2026-07-23 — AppImage, verificador y pulidos

### Objetivo
Crear build AppImage, mejorar verificador de herramientas al inicio, centrar chips.

### Logros
- **AppImage funcional** (4.8MB): empaqueta app + scxctl/stress-ng/hyperfine, usa GTK del sistema
- **Verificador mejorado**: chequea scxctl, stress-ng, hyperfine, run0/sudo; separa críticos de advertencias
- **Chips centrados**: `valign=Gtk.Align.CENTER` en todos los chips `card`+`pill`
- **Build script**: `scripts/build-appimage.sh` — build reproducible

### Problemas con AppImage
- `linuxdeploy-plugin-gtk` no empaqueta correctamente los GResources de GTK4 en Solus
- Los `.gresource` sections existen en las .so pero no se registran al cargar desde el AppDir
- Solución: usar GTK del sistema, solo empaquetar app + binaries. Funciona perfecto.

### GitHub Action + Release
- `.github/workflows/build-appimage.yml` — CI para main y tags v*
- Usa `ARCH=x86_64` para appimagetool
- Release creado con `gh release create` (CLI de GitHub, compatible Node 24)
- `permissions: contents: write` necesario para crear releases
- Primer release exitoso: `v0.7.0` 🎉

### v0.7.4
- Centrados chips de resumen en tendencia

### v0.7.3
- Incluido `data/` en build script del AppImage para empaquetar iconos del IDK
- Version bump a 0.7.3

### v0.7.2
- `data/icons/Adwaita/` — Bundle completo de 44 iconos desde `icon-development-kit`
- `utils/iconos.py` — Módulo centralizado con constantes + `establecer_iconos_idk()`
- Toggle en Controles para elegir entre iconos del IDK o del sistema
- Reemplazados iconos no estándar (`org.gnome.Settings-*`)
- Eliminado logo de distribución en Entorno
- Actualizadas actions de CI a Node.js 24

### v0.7.5
- Icono app en AboutDialog, fallback IDK en modo sistema

---

## 2026-07-29 — v0.8: Squash + Auditoría y Refactorización

> **Nota**: todo el trabajo post-v0.7.5 fue aplanado en un solo commit v0.8.
> Lo que originalmente se versionó como v1.0, v1.1, v1.2 ahora es parte de v0.8.

### Objetivo
Consolidar todo el trabajo post-v0.7.5 en una base estable, auditar el código completo, corregir bugs, refactorizar y hacer el AppImage autocontenido.

### Funcionalidades incorporadas (post-squash)
- **Benchmarks**: stress-ng (cpu, threads, memory) + hyperfine (fork, compile, loaded)
- **Detección automática**: ciclo completo de tests, gestión térmica, scores, ranking
- **Monitor en vivo**: CPU, memoria, temperatura, eventos sched_ext, scxtop
- **Historial**: gráficos de tendencia, ColumnView comparativo, filtros, navegación
- **Internacionalización (i18n)**: español, inglés, francés, alemán, italiano, portugués
- **Iconos portátiles**: GResource con 43 SVGs bundleados, funcionan en cualquier distro
- **AppImage**: empaca app + stress-ng + hyperfine + cyclictest (estáticos), usa GTK del sistema
- **Modularización**: split de automatizacion.py, historial.py, diagnostico.py en subpaquetes
- **RadarChart**: widget Cairo compartido entre Monitor y Entorno
- **Estado centralizado**: dataclasses en core/estado.py reemplazan atributos planos

### Bugs corregidos
- **4 críticos**: `check_same_thread=False` faltante (crash en auto-det), `subprocess` no importado, `set_sensitive(nivel="title")`, guard de re-entrada roto
- **9 altos**: sensor térmico sin try/except, HTML en release_notes, scheduler names traducidos, log tags a rango cero, etc.
- **8 medios**: 4 memory leaks de timers, crosshair en chart, keywords duplicados en lscpu, dead code en scoring, etc.

### God Functions refactorizadas
- `iniciar_auto_test`: 208L → 18L (9 funciones extraídas)
- `_refrescar_tendencia`: 272L → 12L (6 funciones)
- `iniciar_verificacion`: 125L → 25L (3 funciones)
- `sincronizar_sistema`: 41L → 15L (2 funciones)

### DRY — 10 violaciones resueltas (~285 líneas eliminadas)
- `format_raw_value`, chips/leyenda, dot Cairo, child-removal, tarjetas resumen
- subprocess log filter, error handlers benchmark, toast handling, result dict

### AppImage autocontenido
- cyclictest compilado estático + source bundleado para benchmark compile
- gcc/make del sistema (ubicuos), rt-tests source del bundle
- Verificación de integridad del AppImage al arrancar
- Versión inyectada desde git tag en el build

### Build system
- Eliminado `build.sh` (Nuitka, nombrado "scxctl") — puro AppImage
- `pyproject.toml`: backend `build_meta`, version dinámica desde constantes.py
- Archivos muertos en `design/` (7 archivos, ~50KB) eliminados
- `.gitignore` actualizado

### Tests
- 57 tests (49 + 8 nuevos): hybrid.py + calcular_score_categorias

### Problemas encontrados
1. **Reemplazar `crear_chip_leyenda` rompió firma**: cambié parámetros posicionales y los llamadores pasaban `(nombre, grafico, box)` donde el 2do arg ahora es `color_func`. TypeError: 'GraficoComparativo' object is not callable.
2. **GLib.idle_add por scheduler duplicaba chips**: los callbacks pendientes se ejecutaban después de un clear, replicando chips viejos. Fix: batch atómico en un solo idle_add.
3. **SHA256 en descargas**: inviable para continuous/edge tags. Pospuesto para cuando el proyecto madure.

### Aprendizajes
- La firma de funciones compartidas debe cambiarse con kwargs, no posicionales
- GLib.idle_add fraccionado + clear del contenedor = carrera de datos
- gcc es ubicuo en Linux, no vale la pena bundlearlo
- Los tags de git como fuente de versión + inyección en build es más limpio que hardcodear

### Pendiente
- Tests para `verificacion.py`, `estado.py`
- Separar `utils/helpers.py` (grab-bag de 6 grupos)
- `grafico.py`: `self.ocultos` declarado pero nunca usado
