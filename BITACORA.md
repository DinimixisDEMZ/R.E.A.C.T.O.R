# Bitácora de Desarrollo — R.E.A.C.T.O.R

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
