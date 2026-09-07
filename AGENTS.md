# Reactor Project — Agent Session Summary

## Project
Reactor (Reactor Experimental Avanzado Concurrente Telúrico para Optimización de Rendimiento) — A CPU scheduler benchmarking and optimization tool for Linux with `scx` schedulers.

## Hardware de referencia del autor
> Es el equipo donde se desarrolla, **no un requisito**. El proyecto apunta al
> parque de PCs de escritorio y portátiles, viejas y nuevas.

- Lenovo V15 G4 ABP, AMD Ryzen 7 7730U (16 hilos), 16.80 GB RAM
- Solus 4.9, GNOME 50.3, kernel 7.1.3-347.current
- Sin `sudo`; se usa `run0` para las operaciones de root

## Equipo donde se hizo la modernización de 2026
- Acer Nitro AN17-51, Intel Core i7-13620H (**híbrido: 6 P + 4 E, 16 hilos**)
- Arch Linux, kernel 7.2.3-zen1-2-zen, GTK 4.22.4, libadwaita 1.9.3,
  PyGObject 3.56.3, Python 3.14.7
- `scx-scheds 1.1.3-2` instalado; **sin `scx-tools`**, es decir sin `scxctl` ni
  `scx_loader`. Sin `stress-ng` ni `hyperfine`. Es el caso que dejaba la
  aplicación sin arrancar.

## Entry Point
`main.py`

## Current State

### v1.1 (en curso) — Modernización al ecosistema scx 2026 y adaptación al hardware

**El cambio de fondo:** `scxctl` y `scx_loader` salieron del paquete de
planificadores. En Arch, `scx-scheds` trae los 16 planificadores y `scxtop`;
`scxctl`, `scx_loader` y `scxtui` viven en `scx-tools`. Upstream también se
separó: `scx_loader` es ahora el repositorio `sched-ext/scx-loader`.
`main.py` marcaba `scxctl` como dependencia **crítica**, así que la aplicación
hacía `sys.exit(1)` en equipos con el kernel preparado y los planificadores
instalados. Ése es el motivo por el que el proyecto quedó pausado.

**Arquitectura nueva:**
- `core/scx.py` — backends intercambiables: `BackendDbus` (org.scx.Loader,
  autorizado por polkit; el camino preferido), `BackendScxctl`,
  `BackendDirecto` (systemd-run + pkexec/run0) y `BackendSimulado`.
  Se elige el mejor disponible al arrancar. **Nunca se pide la contraseña
  dentro de la aplicación**; `widgets/password_dialog.py` se eliminó.
- `core/sched_ext.py` — observación de `/sys/kernel/sched_ext` sin privilegios.
  Es la fuente de verdad sobre qué gobierna el sistema.
- `core/hardware.py` — topología, híbridos P/E, memoria, energía, chasis,
  virtualización. Todo de `/sys` y `/proc`, nunca de herramientas traducibles.
- `core/dimensionado.py` — parámetros de las pruebas derivados del equipo.
- `core/thermal.py` — reescrito: umbrales derivados del Tjmax del hardware.

### Cosas que conviene no volver a romper

- **Los nombres de planificador tienen dos convenciones.** El kernel publica
  `lavd` en `root/ops`; el loader y `scxctl` usan `scx_lavd`. Todo el código
  trabaja en forma canónica (con prefijo) y sólo la interfaz muestra la corta.
  Usar `core.sched_ext.canonizar()` y `nombre_corto()`.
- **Los modos son de `scx_loader`, no de los planificadores.** Los valores
  enteros son Auto=0, Gaming=1, PowerSave=2, LowLatency=3, Server=4, y **ese
  orden no coincide** con el que mostraba la interfaz antigua. Los modos
  disponibles dependen del planificador y se consultan con `SchedulerModes`.
  Sin loader sólo existe `auto`.
- **`lscpu` traduce sus campos.** Cualquier invocación necesita `LC_ALL=C`.
  Mejor aún: leer `/sys` a través de `core/hardware.py`.
- **Nunca `pkill -f scx_`.** Mataba `scxtop`, `scx_loader` y cualquier proceso
  cuyo comando contuviera esa cadena.
- **Los umbrales térmicos no son constantes.** Salen de `temp*_crit` del
  hardware. 60/75 °C fijos declaraban limitación térmica en portátiles con
  Tjmax de 95-100 °C que estaban perfectamente bien.
- **`os.cpu_count()` no sirve para dimensionar carga.** Ignora la afinidad y la
  cuota de cgroup. Usar `perfil.hilos_para_carga`.
- **En las pruebas de latencia, `valor` es la media en µs y menos es mejor.**
  Cualquier fórmula de gráfico debe invertirlo: el contrato de
  `valor_para_grafico` es "siempre una tasa, más alto es mejor".
- **`scx_chaos` degrada el sistema a propósito** ("designed to amplify race
  conditions"). Está excluido del ciclo automático vía `apto_para_benchmark`,
  junto con `layered` (necesita un JSON de capas), `flow` (levanta un servidor
  web y se autoajusta), `forge`, `mlfq` y `tickless`.

### Improvement Queue (próximas mejoras)
1. ✅ Tests automatizados (240)
2. ✅ Split automatizacion.py
3. ✅ Fix god-object VentanaSimple
4. ✅ `pyproject.toml` (ahora `pip install .` funciona de verdad)
5. ✅ Fix duplicación y constantes
6. ✅ Mejorar excepciones
7. ✅ Arranque sin `scxctl`, control por D-Bus + polkit
8. ✅ Adaptación al hardware (topología, térmica, dimensionado)
9. ⬜ Medición de energía real (RAPL / `power_supply`) en vez del "waste"
   derivado del uso de CPU, que no mide energía pese a llamarse "potencia"
10. ⬜ Fijar o al menos advertir sobre el gobernador de frecuencia durante las
    pruebas: hoy domina el resultado por encima del propio planificador
11. ⬜ Traducciones de-fr-it-pt (36 de 365 cadenas)

### Known Issues / Limitations
- El "score de potencia" no mide energía: se deriva del uso de CPU que reporta
  stress-ng. El nombre es engañoso y está pendiente de resolver (punto 9).
- Las mediciones no fijan el gobernador de frecuencia. Se registra en el
  historial, pero dos corridas con gobernadores distintos no son comparables.
- Alemán, francés, italiano y portugués están al 10 % de traducción.

### Adw.AboutDialog release_notes
**No usa Pango markup.** Usa **AppStream description standards**:
- `<p>` párrafos
- `<ul>` / `<ol>` listas desordenadas/ordenadas
- `<li>` ítems de lista
- `<em>` itálica
- `<code>` monoespaciado
- Texto fuera de estos elementos se ignora.
- No anidar listas.
- Usar `release_notes_version` para mostrar la versión sobre las notas.

### AppImage — Binarios musl
Los binarios de Alpine (musl) son dinámicamente enlazados contra musl.
El intérprete `/lib/ld-musl-x86_64.so.1` no existe en sistemas glibc.
Para bundlearlos en AppImage se necesita un wrapper:
```bash
#!/bin/bash
exec "$HERE/../lib/ld-musl-x86_64.so.1" "$HERE/stress-ng.bin" "$@"
```
Pendiente de implementar post-release.
