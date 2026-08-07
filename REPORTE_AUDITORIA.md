# Reporte de Auditoría y Refactorización — R.E.A.C.T.O.R v0.8

## Resumen Ejecutivo

- **Fecha**: Julio 2026
- **Versión**: 0.8
- **Estado**: Post-auditoría completa con refactorización aplicada

### Métricas

| Métrica | Valor |
| - | - |
| Archivos modificados | 20+ |
| Líneas eliminadas (DRY) | ~285 |
| Bugs críticos corregidos | 4 |
| Bugs altos corregidos | 9 |
| Bugs medios corregidos | 8 |
| Violaciones DRY resueltas | 10 |
| Tests | 49/49 pasando |

---

## 1. Bugs Críticos Corregidos

### 1.1 `core/database.py:78` — `check_same_thread=False` faltante

**Problema**: `guardar_resultados_batch()` llamado desde thread secundario
crasheaba con `ProgrammingError: SQLite objects created in a thread
can only be used in that same thread`.

**Solución**: Agregado `check_same_thread=False` a `sqlite3.connect()`.

### 1.2 `app.py:343` — `subprocess` no importado

**Problema**: `sincronizar_sistema()` captura `subprocess.SubprocessError`
pero `subprocess` nunca se importó en `app.py`.
Lanza `NameError` no capturable.

**Solución**: Agregado `import subprocess` + removido `RE_RUNNING` unused import.

### 1.3 `ui/disponibilidad.py:465` — `set_sensitive(nivel="title")`

**Problema**: `GLib.idle_add(win._btn_verificar_disp.set_sensitive, nivel="title")` pasa `nivel="title"` como keyword arg a `Gtk.Widget.set_sensitive()` que espera booleano.

**Solución**: Cambiado a `set_sensitive(True)`.

### 1.4 `ui/automatizacion/historial.py:140` — Guard de re-entrada roto

**Problema**: `seleccionar_run()` checkea `getattr(win, "_cargando_historial", False)` pero setea `win.auto_state.cargando_historial = True`. Son dos variables distintas.

**Solución**: Unificado a `win.auto_state.cargando_historial`.

---

## 2. Bugs Altos Corregidos

### 2.1 Sensor térmico sin try/except

**Archivo**: `app.py:222-249`

**Problema**: `actualizar_sensor_termico()` sin `try/except`. Una excepción de `obtener_temp()` mata el timer permanentemente vía GLib.

**Solución**: Agregado `try/except` + actualización a "No disponible" en `t == 0`.

### 2.2 HTML en release_notes

**Archivo**: `app.py:260-273`

**Problema**: `Adw.AboutDialog.release_notes` renderiza con Pango markup. Las etiquetas `<p>`, `<ul>`, `<li>` no son soportadas.

**Solución**: Convertido a texto plano con bullets `•`.

### 2.3 `pag_diagnostico` como side-effect

**Archivo**: `app.py:101` / `diagnostico/pagina.py:68`

**Problema**: `configurar_ui_diagnostico(self)` asignaba `win.pag_diagnostico` como efecto secundario en vez de retornarlo.

**Solución**: Refactor: retorna el NavigationPage, `__init__` lo asigna explícitamente.

### 2.4 Animation widget incorrecto

**Archivo**: `ui/automatizacion/pesos.py:32`

**Problema**: Las 3 animaciones usaban `win.slider_pot` como lifecycle widget.

**Solución**: Iteración ahora pasa cada slider individual por separado.

### 2.5 Scheduler names en `traducir()`

**Archivo**: `ui/automatizacion/deteccion.py:209,236,314`

**Problema**: Nombres de scheduler (`scx_bpfland`, etc.) pasados a `traducir()`, devolviendo string vacío.

**Solución**: Sacados de `traducir()`, se muestran como identificadores técnicos.

### 2.6 Log tags aplicados a rango cero

**Archivo**: `utils/helpers.py:62-70`

**Problema**: `buf.insert()` avanza el iterador después del texto. `buf.apply_tag()` con ese iterador cubre rango cero. Los colores de log nunca se veían.

**Solución**: Capturado offset antes del insert + refactor DRY con `_scroll_log()`.

### 2.7 `NOMBRES_IDIOMA` key mismatch

**Archivo**: `utils/i18n.py:109-119`

**Problema**: Códigos cortos (`"es"`) vs lookup con `split("_")[0]` desde códigos completos (`"es_ES"`). `"pt_BR"` se rompía.

**Solución**: `pt_BR` → `pt`, removidas entradas muertas (`ca`, `eu`, `gl`).

### 2.8 `grep -oP` + fallback sistema

**Archivo**: `scripts/build-appimage.sh:52`

**Problema**: `grep -oP` es GNU-only. Fallback a stress-ng del sistema (dinámico) no portable.

**Solución**: `grep -oE`, `sort -t.` portable, quitado fallback a binario dinámico.

---

## 3. Bugs Medios Corregidos

### 3.1 Memory Leaks — Timers sin cleanup (4 ocurrencias)

| Archivo | Timer | Solución |
| --------- | ------- | ---------- |
| `ui/grafico.py:60` | Tick animación | `self._tick_source` + cleanup en `"destroy"` |
| `ui/diagnostico/pagina.py:71` | Monitoreo 1.5s | `pag._monitor_source` + cleanup en `"destroy"` |
| `ui/automatizacion/deteccion.py:82` | Animación auto-test | `win.auto_state.anim_timer` + cleanup en `finalizar_auto_test()` |
| `ui/verificacion.py:146,164` | Barra progreso | `barra_source[0]` + `_limpiar_timer()` en `"closed"` |

### 3.2 Crosshair/tooltip dimensión incorrecta

**Archivo**: `ui/historial/dibujo.py:316,359`

**Problema**: Cruzaba el chart con `ancho_trazo` en vez de `alto_trazo`.

**Solución**: `alto_trazo` pasado como parámetro.

### 3.3 Keywords duplicados en categorías lscpu

**Archivo**: `ui/historial/entorno.py:200-213`

**Problema**: Keywords de vulnerabilidad en 2 categorías. Match en orden incorrecto.

**Solución**: Sacados del primer par (queda vacío — el catch-all existe vía `else`).

### 3.4 Dead computation en scoring

**Archivo**: `core/scoring.py:134,137,143-144`

**Problema**: `throughput_per_core` y `efficiency` computados pero nunca leídos.

**Solución**: Eliminados.

### 3.5 Acceso directo a dict sin `.get()`

**Archivo**: `core/database.py:166,180`

**Problema**: `result["valor"]`, `r["sched"]`, `r["tipo"]` lanzan `KeyError` si falta la clave.

**Solución**: Cambiados a `.get()` con defaults.

---

## 4. God Functions Refactorizadas

| Función | Archivo | Antes | Después | Funciones extraídas |
| --------- | --------- | ------- | --------- | --------------------- |
| `iniciar_auto_test` | `deteccion.py:48-256` | 208L | 18L | 9 funciones |
| `_refrescar_tendencia` | `tendencia.py:137-409` | 272L | 12L | 6 funciones |
| `iniciar_verificacion` | `disponibilidad.py:345-470` | 125L | 25L | 3 funciones |
| `sincronizar_sistema` | `app.py:304-345` | 41L | 15L | 2 funciones |

---

## 5. Violaciones DRY Resueltas

| # | Refactor | Archivos | Líneas eliminadas |
| --- | ---------- | ---------- | ------------------- |
| 1 | `format_raw_value` | `grafico.py` → `helpers.py` | −24 |
| 2 | Chips/leyenda generalizados | 4 archivos → `widgets/legend.py` | −120+ |
| 3 | Dot Cairo (`dibujar_dot`) | 5 archivos → `helpers.py` | −30 |
| 4 | Child-removal (`vaciar_contenedor`) | 6 archivos (11 ocurrencias) → `helpers.py` | −22 |
| 5 | `crear_chip_informativo` | 2 archivos → `widgets/legend.py` | −30 |
| 6 | Subprocess log filter | `benchmark.py` + `hybrid.py` → `helpers.py` | −20 |
| 7 | Error handlers benchmark | `hybrid.py` → `helpers.py` | −15 |
| 8 | Toast error handling | `controles.py` + `deteccion.py` → `helpers.py` | −20 |
| 9 | Benchmark result dict | `benchmark.py` + `hybrid.py` → `helpers.py` | −30 |

**Total**: ~285 líneas eliminadas

---

## 6. Archivos Muertos Eliminados

| Archivo | Tamaño | Razón |
| --------- | -------- | ------- |
| `design/icon_positions.py` | 5.5 KB | Legacy UI experiment |
| `design/stats_table.py` | 6.3 KB | Legacy UI experiment |
| `design/sched_info.py` | 6.3 KB | Legacy UI experiment |
| `design/history_flyout.py` | 6.1 KB | Legacy UI experiment |
| `design/historial_results.py` | 12.1 KB | Legacy UI experiment |
| `design/historial_results_rows.py` | 6.0 KB | Legacy UI experiment |
| `design/vte_colors.py` | 6.6 KB | Legacy UI experiment |
| `po/*/LC_MESSAGES/*.po~` | 6 archivos | Backup files commitados |

---

## 7. Deuda Técnica Restante

### Build System

- `pyproject.toml` — versión duplicada en `core/constantes.py` (no single-source)
- `scripts/build-appimage.sh` — sin verificación SHA256 de descargas
- `.github/workflows/build-appimage.yml` — sin step de `pytest` pre-build

### Tests

- `test_scoring.py` — `calcular_score_categorias` nunca testeado directamente
- Sin tests para `hybrid.py`, `verificacion.py`, `estado.py`
- Tests con aserciones débiles (solo counts, no contenido)

### Código

- `ui/grafico.py` — `ocultos` set declarado pero nunca poblado/leído
- `utils/helpers.py` — 6 grupos de funcionalidad no relacionada en un solo archivo
- `core/database.py` — metadatos de schedulers mezclados en capa de persistencia

### Configuración

- `.github/workflows/build-appimage.yml` — sin cache para appimagetool/rt-tests
- `build.sh` — nombrado `scxctl` (engañoso)
- `AGENTS.md` — paths referencian `/home/dinimixis/Documentos/...` que ya no existe
