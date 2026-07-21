import json
import inspect
from pathlib import Path
import shlex
import subprocess
import sys
import types

import pytest


# Los modulos productivos solo necesitan estas dos funciones de helpers. Este
# stub evita importar PyGObject en entornos de CI sin GTK.
helpers_stub = types.ModuleType("utils.helpers")
helpers_stub.log = lambda *_args, **_kwargs: None
helpers_stub.limpiar_texto = lambda texto: texto or ""
helpers_previo = sys.modules.get("utils.helpers")
sys.modules["utils.helpers"] = helpers_stub
try:
    from core import benchmark, hybrid, processes  # noqa: E402
    from core.operations import CancellationToken, OperationCancelled  # noqa: E402
    from core.scx import ScxState  # noqa: E402
finally:
    if helpers_previo is None:
        del sys.modules["utils.helpers"]
    else:
        sys.modules["utils.helpers"] = helpers_previo


STRESS_YAML = """\
---
metrics:
  - stressor: switch
    bogo-ops: 1.2e4
    bogo-ops-per-second-real-time: 2.4e3
    bogo-ops-per-second-usr-sys-time: 1.5e2
    cpu-usage-per-instance: 8.0e1
    wall-clock-time: 5.0
    nanosecs-per-context-switch-pipe-method: 2.5e3
    rejected-nan: .nan
    rejected-inf: +Inf
    rejected-overflow: 1e999
  - stressor: cpu
    bogo-ops: 1.25e4
    bogo-ops-per-second-real-time: 2.5e3
    bogo-ops-per-second-usr-sys-time: 1.6e2
    cpu-usage-per-instance: 7.5e1
    wall-clock-time: 5.0
  - stressor: mutex
    bogo-ops: 1.1e4
    bogo-ops-per-second-real-time: 2.2e3
    bogo-ops-per-second-usr-sys-time: 1.4e2
    cpu-usage-per-instance: 7.0e1
    wall-clock-time: 5.0
    nanosecs-per-mutex: 4.2e3
outside-metrics: 999
...
"""


HYPERFINE_JSON = {
    "results": [
        {
            "mean": 10.5e-6,
            "stddev": 2.0e-6,
            "min": 1.0e-6,
            "max": 20.0e-6,
            "times": [value * 1e-6 for value in range(1, 21)],
        }
    ]
}


class FakeScxManager:
    def __init__(self, estado=None):
        self.estado = estado or ScxState("scx_test", "auto")
        self.capture_count = 0
        self.capture_tokens = []

    def capturar_estado(self, cancel_token=None):
        self.capture_count += 1
        self.capture_tokens.append(cancel_token)
        return self.estado


class MissingScxManager:
    def capturar_estado(self, cancel_token=None):
        raise FileNotFoundError(2, "missing", "scxctl")


class FakeLoadProcess:
    def __init__(self):
        self.pid = 43210
        self.terminated = False
        self.killed = False
        self.waits = []

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return 0


class DyingLoadProcess(FakeLoadProcess):
    def __init__(self):
        super().__init__()
        self.poll_count = 0

    def poll(self):
        self.poll_count += 1
        return None if self.poll_count == 1 else 1


class NonBlockingCancellationToken(CancellationToken):
    def __init__(self):
        super().__init__()
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.raise_if_cancelled()
        return False


class CancelOnWaitToken(CancellationToken):
    def wait(self, timeout=None):
        self.cancel()
        return True


class ControlledProcess:
    def __init__(self, communicate_action):
        self.pid = 24680
        self.returncode = None
        self.communicate_timeouts = []
        self.waits = []
        self.terminated = False
        self.killed = False
        self._communicate_action = communicate_action

    def communicate(self, timeout=None):
        self.communicate_timeouts.append(timeout)
        return self._communicate_action(self)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class ArtifactProcess:
    def __init__(self, command):
        self.command = command
        self.pid = 13579
        self.returncode = None
        self.communicated = False

    def communicate(self, timeout=None):
        if not self.communicated:
            if "--yaml" in self.command:
                path = Path(self.command[self.command.index("--yaml") + 1])
                path.write_text(STRESS_YAML, encoding="utf-8")
            if "--export-json" in self.command:
                path = Path(self.command[self.command.index("--export-json") + 1])
                path.write_text(json.dumps(HYPERFINE_JSON), encoding="utf-8")
            self.communicated = True
        self.returncode = 0
        return "", ""

    def terminate(self):
        self.returncode = -processes._SIGTERM

    def kill(self):
        self.returncode = -processes._SIGKILL

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


@pytest.fixture(autouse=True)
def no_real_sleeps(monkeypatch):
    monkeypatch.setattr(processes.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(processes, "_POSIX_PROCESS_GROUPS", False)


def _habilitar_binarios(monkeypatch):
    rutas = {
        "stress-ng": "/mock/bin/stress-ng",
        "hyperfine": "/mock/bin/hyperfine",
    }
    resolver = lambda name: rutas.get(name)
    monkeypatch.setattr(benchmark.shutil, "which", resolver)
    monkeypatch.setattr(hybrid.shutil, "which", resolver)
    monkeypatch.setattr(hybrid, "_resolver_compilador", lambda: "/mock/bin/cc")
    return rutas


def _fake_stress_run(paths, contenido=STRESS_YAML, returncode=0):
    def ejecutar(cmd, **_kwargs):
        yaml_path = Path(cmd[cmd.index("--yaml") + 1])
        paths.append(yaml_path)
        if returncode == 0:
            yaml_path.write_text(contenido, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode, "", "fallo simulado")

    return ejecutar


def _fake_hyperfine_run(paths, data=HYPERFINE_JSON, returncode=0):
    def ejecutar(cmd, **_kwargs):
        json_path = Path(cmd[cmd.index("--export-json") + 1])
        paths.append(json_path)
        if returncode == 0:
            json_path.write_text(json.dumps(data), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode, "", "fallo simulado")

    return ejecutar


def test_real_engine_signatures_accept_cancel_token_at_the_end():
    nombres = [
        "tipo",
        "scx_manager",
        "tv_log",
        "tiempo",
        "logs",
        "modo_dev",
        "cancel_token",
    ]
    for engine in (benchmark.correr_benchmark, hybrid.correr_hybrid):
        firma = inspect.signature(engine)
        assert list(firma.parameters) == nombres
        assert firma.parameters["cancel_token"].default is None

    token = NonBlockingCancellationToken()
    stress = benchmark.correr_benchmark(
        "cpu",
        FakeScxManager(),
        None,
        5,
        False,
        True,
        cancel_token=token,
    )
    latency = hybrid.correr_hybrid(
        "fork",
        FakeScxManager(),
        None,
        5,
        False,
        True,
        cancel_token=token,
    )

    assert stress["tipo"] == "cpu"
    assert latency["tipo"] == "latencia_fork"
    assert token.waits == [0.3, 0.5, 0.3, 0.5]


@pytest.mark.parametrize(
    ("module", "engine", "tipo"),
    [
        (benchmark, benchmark.correr_benchmark, "cpu"),
        (hybrid, hybrid.correr_hybrid, "fork"),
    ],
)
def test_engines_require_typed_scx_state(monkeypatch, module, engine, tipo):
    class UntypedManager:
        def capturar_estado(self, cancel_token=None):
            return "scx_test", "auto"

    mensajes = []
    monkeypatch.setattr(
        module,
        "_log",
        lambda _tv, texto, *_args, **_kwargs: mensajes.append(texto),
    )

    assert engine(tipo, UntypedManager(), object(), modo_dev=True) is None
    assert any("ScxState" in mensaje for mensaje in mensajes)


@pytest.mark.parametrize(
    ("engine", "tipo"),
    [
        (benchmark.correr_benchmark, "cpu"),
        (hybrid.correr_hybrid, "fork"),
    ],
)
def test_dev_mode_uses_one_stable_simulated_state(engine, tipo):
    manager = FakeScxManager(ScxState("scx_lavd", "gaming"))

    resultado = engine(tipo, manager, logs=False, modo_dev=True)

    assert resultado["sched"] == "scx_lavd"
    assert resultado["modo"] == "gaming"
    assert manager.capture_count == 1


@pytest.mark.parametrize(
    ("engine", "tipo"),
    [
        (benchmark.correr_benchmark, "cpu"),
        (hybrid.correr_hybrid, "fork"),
    ],
)
def test_engines_propagate_cancellation_before_start(engine, tipo):
    token = CancellationToken()
    token.cancel()

    with pytest.raises(OperationCancelled):
        engine(tipo, FakeScxManager(), logs=False, cancel_token=token)


@pytest.mark.parametrize(
    ("engine", "tipo"),
    [
        (benchmark.correr_benchmark, "cpu"),
        (hybrid.correr_hybrid, "fork"),
    ],
)
def test_engines_propagate_cancellation_during_initial_wait(engine, tipo):
    with pytest.raises(OperationCancelled):
        engine(
            tipo,
            FakeScxManager(),
            logs=False,
            cancel_token=CancelOnWaitToken(),
        )


@pytest.mark.parametrize(
    ("module", "engine", "tipo"),
    [
        (benchmark, benchmark.correr_benchmark, "cpu"),
        (hybrid, hybrid.correr_hybrid, "fork"),
    ],
)
def test_engines_check_cancellation_before_returning_result(
    monkeypatch,
    module,
    engine,
    tipo,
):
    token = NonBlockingCancellationToken()
    original_result = module._resultado_dev

    def cancel_before_return(*args):
        result = original_result(*args)
        token.cancel()
        return result

    monkeypatch.setattr(module, "_resultado_dev", cancel_before_return)

    with pytest.raises(OperationCancelled):
        engine(
            tipo,
            FakeScxManager(),
            logs=False,
            modo_dev=True,
            cancel_token=token,
        )


def test_process_runner_does_not_spawn_when_already_cancelled(monkeypatch):
    token = CancellationToken()
    token.cancel()

    def forbidden_popen(*_args, **_kwargs):
        raise AssertionError("Popen no debe ejecutarse tras cancelar")

    monkeypatch.setattr(processes.subprocess, "Popen", forbidden_popen)

    with pytest.raises(OperationCancelled):
        processes.run_process(["tool"], cancel_token=token)


def test_process_runner_collects_stdout_and_stderr(monkeypatch):
    def communicate_action(process):
        process.returncode = 7
        return "salida", "error"

    process = ControlledProcess(communicate_action)
    popen_kwargs = []

    def popen(_command, **kwargs):
        popen_kwargs.append(kwargs)
        return process

    monkeypatch.setattr(processes.subprocess, "Popen", popen)

    result = processes.run_process(["tool"], timeout=1)

    assert result.returncode == 7
    assert result.stdout == "salida"
    assert result.stderr == "error"
    assert popen_kwargs[0]["stdout"] == subprocess.PIPE
    assert popen_kwargs[0]["stderr"] == subprocess.PIPE


def test_process_runner_cancels_in_flight_and_reaps_group(monkeypatch):
    token = CancellationToken()
    signals = []
    popen_kwargs = []

    def communicate_action(process):
        if len(process.communicate_timeouts) == 1:
            token.cancel()
            raise subprocess.TimeoutExpired(["tool"], 0.01)
        process.returncode = -processes._SIGTERM
        return "", ""

    process = ControlledProcess(communicate_action)

    def popen(_command, **kwargs):
        popen_kwargs.append(kwargs)
        return process

    monkeypatch.setattr(processes, "_POSIX_PROCESS_GROUPS", True)
    monkeypatch.setattr(processes.subprocess, "Popen", popen)
    monkeypatch.setattr(
        processes.os,
        "killpg",
        lambda pid, sig: signals.append((pid, sig)),
        raising=False,
    )

    with pytest.raises(OperationCancelled):
        processes.run_process(
            ["tool"],
            cancel_token=token,
            poll_interval=0.01,
        )

    assert popen_kwargs[0]["start_new_session"] is True
    assert signals == [
        (process.pid, processes._SIGTERM),
        (process.pid, processes._SIGKILL),
    ]
    assert len(process.communicate_timeouts) == 2


def test_process_runner_timeout_uses_term_then_kill_on_group(monkeypatch):
    signals = []
    popen_kwargs = []

    def communicate_action(process):
        if len(process.communicate_timeouts) == 1:
            raise subprocess.TimeoutExpired(["tool"], 1)
        process.returncode = -processes._SIGKILL
        return "", ""

    process = ControlledProcess(communicate_action)

    def popen(_command, **kwargs):
        popen_kwargs.append(kwargs)
        return process

    monkeypatch.setattr(processes, "_POSIX_PROCESS_GROUPS", True)
    monkeypatch.setattr(processes.subprocess, "Popen", popen)
    monkeypatch.setattr(
        processes.os,
        "killpg",
        lambda pid, sig: signals.append((pid, sig)),
        raising=False,
    )

    with pytest.raises(subprocess.TimeoutExpired):
        processes.run_process(["tool"], timeout=0)

    assert popen_kwargs[0]["start_new_session"] is True
    assert signals == [
        (process.pid, processes._SIGTERM),
        (process.pid, processes._SIGKILL),
    ]
    assert len(process.communicate_timeouts) == 2


def test_process_runner_remains_bounded_if_kill_wait_times_out(monkeypatch):
    signals = []

    def communicate_action(process):
        raise subprocess.TimeoutExpired(
            ["tool"],
            1,
            output=f"parcial-{len(process.communicate_timeouts)}",
            stderr="error",
        )

    process = ControlledProcess(communicate_action)
    monkeypatch.setattr(processes, "_POSIX_PROCESS_GROUPS", True)
    monkeypatch.setattr(
        processes.subprocess,
        "Popen",
        lambda _command, **_kwargs: process,
    )
    monkeypatch.setattr(
        processes.os,
        "killpg",
        lambda pid, sig: signals.append((pid, sig)),
        raising=False,
    )

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        processes.run_process(["tool"], timeout=0)

    assert signals == [
        (process.pid, processes._SIGTERM),
        (process.pid, processes._SIGKILL),
    ]
    assert process.waits == []
    assert caught.value.output == "parcial-2"
    assert caught.value.stderr == "error"


def test_process_runner_has_safe_non_posix_fallback(monkeypatch):
    def communicate_action(process):
        if len(process.communicate_timeouts) == 1:
            raise subprocess.TimeoutExpired(["tool"], 1)
        process.returncode = -processes._SIGKILL
        return "", ""

    process = ControlledProcess(communicate_action)
    monkeypatch.setattr(processes, "_POSIX_PROCESS_GROUPS", False)
    monkeypatch.setattr(
        processes.subprocess,
        "Popen",
        lambda _command, **_kwargs: process,
    )

    with pytest.raises(subprocess.TimeoutExpired):
        processes.run_process(["tool"], timeout=0)

    assert process.terminated
    assert process.killed


def test_real_engines_use_popen_runner_and_never_subprocess_run(monkeypatch):
    _habilitar_binarios(monkeypatch)
    commands = []
    popen_kwargs = []

    def popen(command, **kwargs):
        commands.append(command)
        popen_kwargs.append(kwargs)
        return ArtifactProcess(command)

    def forbidden_run(*_args, **_kwargs):
        raise AssertionError("Los motores no deben ejecutar subprocess.run")

    monkeypatch.setattr(processes, "_POSIX_PROCESS_GROUPS", True)
    monkeypatch.setattr(processes.subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", forbidden_run)

    stress = benchmark.correr_benchmark("cpu", FakeScxManager(), logs=False)
    latency = hybrid.correr_hybrid("fork", FakeScxManager(), logs=False)

    assert stress["tipo"] == "cpu"
    assert latency["tipo"] == "latencia_fork"
    assert [command[0] for command in commands] == ["stress-ng", "hyperfine"]
    assert all(kwargs["start_new_session"] is True for kwargs in popen_kwargs)


@pytest.mark.parametrize(
    ("tipo", "minimum"),
    [("fork", 1_000), ("compile", 20), ("loaded", 20)],
)
def test_hyperfine_budget_controls_runs_and_timeout(tipo, minimum):
    short_runs, short_timeout = hybrid._planificar_hyperfine(tipo, 1)
    long_runs, long_timeout = hybrid._planificar_hyperfine(tipo, 10)

    assert short_runs >= minimum
    assert long_runs > short_runs
    assert long_timeout > short_timeout


def test_parser_respects_blocks_indentation_and_finite_numbers():
    switch = benchmark._parsear_yaml_simple(STRESS_YAML, stressor="switch")
    cpu = benchmark._parsear_yaml_simple(STRESS_YAML, stressor="cpu")
    mutex = benchmark._parsear_yaml_simple(STRESS_YAML, stressor="mutex")

    assert switch["stressor"] == "switch"
    assert switch["bogo-ops"] == 12_000.0
    assert switch["nanosecs-per-context-switch-pipe-method"] == 2_500.0
    assert "rejected-nan" not in switch
    assert "rejected-inf" not in switch
    assert "rejected-overflow" not in switch
    assert "outside-metrics" not in switch
    assert cpu["bogo-ops"] == 12_500.0
    assert mutex["nanosecs-per-mutex"] == 4_200.0


def test_parser_handles_multiple_metrics_fixtures_without_mixing():
    contenido = """\
fixture_one:
  metrics:
    - stressor: switch
      bogo-ops: 10
fixture_two:
  metrics:
    - stressor: mutex
      bogo-ops: 20
      nested:
        bogo-ops: 999
"""

    assert benchmark._parsear_yaml_simple(contenido, "switch")["bogo-ops"] == 10
    assert benchmark._parsear_yaml_simple(contenido, "mutex")["bogo-ops"] == 20
    assert benchmark._parsear_yaml_simple(contenido, "missing") == {}


def test_p95_uses_deterministic_nearest_rank():
    muestras = list(range(1, 21))
    assert hybrid._calcular_p95(muestras) == 19
    assert hybrid._calcular_p95(list(reversed(muestras))) == 19
    assert hybrid._calcular_p95([7]) == 7
    with pytest.raises(ValueError):
        hybrid._calcular_p95([])
    with pytest.raises(ValueError):
        hybrid._calcular_p95([1, float("nan")])


def test_hyperfine_parser_requires_times_and_preserves_statistics():
    metricas = hybrid._extraer_metricas_hyperfine(HYPERFINE_JSON)

    assert metricas["p95_us"] == pytest.approx(19.0)
    assert metricas["mean_us"] == pytest.approx(10.5)
    assert metricas["std_us"] == pytest.approx(2.0)
    assert metricas["min_us"] == pytest.approx(1.0)
    assert metricas["max_us"] == pytest.approx(20.0)
    assert metricas["runs"] == 20
    assert hybrid._extraer_metricas_hyperfine({"results": [{"mean": 1.0}]}) is None


@pytest.mark.parametrize(
    ("tipo", "response", "response_kind"),
    [
        ("cpu", 2.5, "mean_context_switch_us"),
        ("threads", None, None),
        ("memory", 4.2, "mean_mutex_us"),
    ],
)
def test_stress_results_follow_response_contract(
    monkeypatch,
    tipo,
    response,
    response_kind,
):
    _habilitar_binarios(monkeypatch)
    paths = []
    monkeypatch.setattr(benchmark, "run_process", _fake_stress_run(paths))

    resultado = benchmark.correr_benchmark(
        tipo,
        FakeScxManager(),
        logs=False,
    )

    assert resultado["tipo"] == tipo
    if response is None:
        assert resultado["response"] is None
    else:
        assert resultado["response"] == pytest.approx(response)
    assert resultado["response_kind"] == response_kind
    assert resultado["p95"] is None
    assert resultado["fairness_kind"] == "cpu_idle_fraction"
    assert resultado["valor"] > 0
    assert paths and not paths[0].parent.exists()


def test_stress_measurement_fails_without_required_response(monkeypatch):
    _habilitar_binarios(monkeypatch)
    contenido = STRESS_YAML.replace(
        "nanosecs-per-mutex: 4.2e3",
        "nanosecs-per-mutex: .nan",
    )
    monkeypatch.setattr(
        benchmark,
        "run_process",
        _fake_stress_run([], contenido=contenido),
    )

    assert benchmark.correr_benchmark("memory", FakeScxManager(), logs=False) is None


def test_stress_measurement_fails_without_cpu_usage(monkeypatch):
    _habilitar_binarios(monkeypatch)
    contenido = STRESS_YAML.replace(
        "    cpu-usage-per-instance: 8.0e1\n",
        "",
        1,
    )
    monkeypatch.setattr(
        benchmark,
        "run_process",
        _fake_stress_run([], contenido=contenido),
    )

    assert benchmark.correr_benchmark("cpu", FakeScxManager(), logs=False) is None


def test_threads_measurement_fails_without_backed_throughput(monkeypatch):
    _habilitar_binarios(monkeypatch)
    contenido = """\
metrics:
  - stressor: cpu
    bogo-ops: 0
    bogo-ops-per-second-real-time: 0
    wall-clock-time: 0
"""
    monkeypatch.setattr(
        benchmark,
        "run_process",
        _fake_stress_run([], contenido=contenido),
    )

    assert benchmark.correr_benchmark("threads", FakeScxManager(), logs=False) is None


def test_stress_cancellation_propagates_and_cleans_tempdir(monkeypatch):
    _habilitar_binarios(monkeypatch)
    token = NonBlockingCancellationToken()
    paths = []

    def cancelar(cmd, **kwargs):
        paths.append(Path(cmd[cmd.index("--yaml") + 1]))
        assert kwargs["cancel_token"] is token
        token.cancel()
        raise OperationCancelled("cancelado durante stress-ng")

    monkeypatch.setattr(benchmark, "run_process", cancelar)

    with pytest.raises(OperationCancelled):
        benchmark.correr_benchmark(
            "cpu",
            FakeScxManager(),
            logs=False,
            cancel_token=token,
        )

    assert paths and not paths[0].parent.exists()


@pytest.mark.parametrize("fallo", ["nonzero", "timeout"])
def test_stress_tempdir_is_removed_on_all_failures(monkeypatch, fallo):
    _habilitar_binarios(monkeypatch)
    paths = []

    def ejecutar(cmd, **kwargs):
        yaml_path = Path(cmd[cmd.index("--yaml") + 1])
        paths.append(yaml_path)
        if fallo == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return subprocess.CompletedProcess(cmd, 1, "", "fallo")

    monkeypatch.setattr(benchmark, "run_process", ejecutar)

    assert benchmark.correr_benchmark("cpu", FakeScxManager(), logs=False) is None
    assert paths and not paths[0].parent.exists()


@pytest.mark.parametrize("tipo", ["fork", "compile", "loaded"])
def test_hybrid_results_use_real_p95_and_canonical_type(monkeypatch, tipo):
    _habilitar_binarios(monkeypatch)
    paths = []
    commands = []
    load_process = FakeLoadProcess()
    load_commands = []

    def ejecutar(cmd, **kwargs):
        commands.append(cmd)
        return _fake_hyperfine_run(paths)(cmd, **kwargs)

    def popen(cmd, **kwargs):
        load_commands.append((cmd, kwargs))
        return load_process

    monkeypatch.setattr(hybrid, "run_process", ejecutar)
    monkeypatch.setattr(hybrid, "start_process", popen)

    resultado = hybrid.correr_hybrid(tipo, FakeScxManager(), logs=False)

    assert resultado["tipo"] == f"latencia_{tipo}"
    assert resultado["response"] == pytest.approx(19.0)
    assert resultado["response_kind"] == "p95_us"
    assert resultado["p95"] == pytest.approx(19.0)
    assert resultado["p95"] != resultado["std_us"]
    assert resultado["p95"] != resultado["max_us"]
    assert resultado["fairness_kind"] == "coefficient_of_variation"
    assert resultado["mean_us"] == pytest.approx(10.5)
    assert resultado["min_us"] == pytest.approx(1.0)
    assert resultado["max_us"] == pytest.approx(20.0)
    assert paths and not paths[0].parent.exists()

    flat_command = " ".join(commands[0])
    assert "pkill" not in flat_command
    if tipo == "loaded":
        assert load_commands[0][0][0] == "stress-ng"
        assert load_commands[0][1]["cancel_token"] is None
        timeout_index = load_commands[0][0].index("--timeout") + 1
        timeout_carga = int(load_commands[0][0][timeout_index].removesuffix("s"))
        _, timeout_hyperfine = hybrid._planificar_hyperfine("loaded", 5)
        assert timeout_carga == hybrid._calcular_timeout_carga(timeout_hyperfine)
        assert timeout_carga <= hybrid._MAX_LOADED_STRESS_TIMEOUT
        assert load_process.waits
        assert load_process.terminated
        runs_index = commands[0].index("-r") + 1
        assert int(commands[0][runs_index]) >= 20
    elif tipo == "compile":
        runs_index = commands[0].index("-r") + 1
        assert commands[0][runs_index] == "20"
    else:
        assert not load_commands


def test_loaded_stress_timeout_is_derived_and_hard_bounded():
    for presupuesto in (1, 5, 60, 10_000):
        _, timeout_hyperfine = hybrid._planificar_hyperfine("loaded", presupuesto)
        timeout_carga = hybrid._calcular_timeout_carga(timeout_hyperfine)
        assert timeout_carga > timeout_hyperfine
        assert timeout_carga <= hybrid._MAX_LOADED_STRESS_TIMEOUT

    assert (
        hybrid._calcular_timeout_carga(10_000)
        == hybrid._MAX_LOADED_STRESS_TIMEOUT
    )


def test_real_engines_discard_result_when_final_scx_state_changes(monkeypatch):
    _habilitar_binarios(monkeypatch)
    mensajes = []

    class ChangingManager:
        def __init__(self, final):
            self.states = [ScxState("scx_test", "auto"), final]
            self.capture_count = 0
            self.capture_tokens = []

        def capturar_estado(self, cancel_token=None):
            state = self.states[min(self.capture_count, len(self.states) - 1)]
            self.capture_count += 1
            self.capture_tokens.append(cancel_token)
            return state

    monkeypatch.setattr(
        benchmark,
        "_log",
        lambda _tv, texto, *_args, **_kwargs: mensajes.append(texto),
    )
    monkeypatch.setattr(
        hybrid,
        "_log",
        lambda _tv, texto, *_args, **_kwargs: mensajes.append(texto),
    )
    monkeypatch.setattr(benchmark, "run_process", _fake_stress_run([]))
    monkeypatch.setattr(hybrid, "run_process", _fake_hyperfine_run([]))
    stress_manager = ChangingManager(ScxState("scx_other", "auto"))
    hybrid_manager = ChangingManager(ScxState("scx_test", "gaming"))

    token = NonBlockingCancellationToken()
    assert (
        benchmark.correr_benchmark(
            "cpu",
            stress_manager,
            object(),
            cancel_token=token,
        )
        is None
    )
    assert (
        hybrid.correr_hybrid(
            "fork",
            hybrid_manager,
            object(),
            cancel_token=token,
        )
        is None
    )

    assert stress_manager.capture_count == 2
    assert hybrid_manager.capture_count == 2
    assert stress_manager.capture_tokens == [token, token]
    assert hybrid_manager.capture_tokens == [token, token]
    descartes = [
        mensaje for mensaje in mensajes if "Resultado descartado" in mensaje
    ]
    assert len(descartes) == 2
    assert any("scx_other" in mensaje for mensaje in descartes)
    assert any("gaming" in mensaje for mensaje in descartes)


def test_process_output_is_logged_in_bounded_batches(monkeypatch):
    _habilitar_binarios(monkeypatch)
    stress_logs = []
    hybrid_logs = []

    def stress_run(cmd, **_kwargs):
        yaml_path = Path(cmd[cmd.index("--yaml") + 1])
        yaml_path.write_text(STRESS_YAML, encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd,
            0,
            "\n".join(f"out-{index}" for index in range(250)),
            "\n".join(f"err-{index}" for index in range(250)),
        )

    def hyperfine_run(cmd, **_kwargs):
        json_path = Path(cmd[cmd.index("--export-json") + 1])
        json_path.write_text(json.dumps(HYPERFINE_JSON), encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd,
            0,
            "\n".join(f"hout-{index}" for index in range(40)),
            "\n".join(f"herr-{index}" for index in range(40)),
        )

    monkeypatch.setattr(benchmark, "run_process", stress_run)
    monkeypatch.setattr(hybrid, "run_process", hyperfine_run)
    monkeypatch.setattr(
        benchmark,
        "_log",
        lambda _tv, texto, *_args, **_kwargs: stress_logs.append(texto),
    )
    monkeypatch.setattr(
        hybrid,
        "_log",
        lambda _tv, texto, *_args, **_kwargs: hybrid_logs.append(texto),
    )

    assert benchmark.correr_benchmark("cpu", FakeScxManager(), object())
    assert hybrid.correr_hybrid("fork", FakeScxManager(), object())

    stress_stdout = [texto for texto in stress_logs if "STDOUT: out-" in texto]
    stress_stderr = [texto for texto in stress_logs if "STDERR: err-" in texto]
    hybrid_stdout = [texto for texto in hybrid_logs if "STDOUT: hout-" in texto]
    hybrid_stderr = [texto for texto in hybrid_logs if "STDERR: herr-" in texto]
    assert len(stress_stdout) == len(stress_stderr) == 1
    assert stress_stdout[0].count("STDOUT: ") == 200
    assert stress_stderr[0].count("STDERR: ") == 200
    assert "out-200" not in stress_stdout[0]
    assert "err-200" not in stress_stderr[0]
    assert len(hybrid_stdout) == len(hybrid_stderr) == 1
    assert hybrid_stdout[0].count("STDOUT: ") == 30
    assert hybrid_stderr[0].count("STDERR: ") == 30
    assert "hout-30" not in hybrid_stdout[0]
    assert "herr-30" not in hybrid_stderr[0]


def test_hybrid_fails_when_json_has_no_samples_and_cleans_temp(monkeypatch):
    _habilitar_binarios(monkeypatch)
    paths = []
    sin_times = {"results": [{"mean": 1e-6, "stddev": 1e-7}]}
    monkeypatch.setattr(
        hybrid,
        "run_process",
        _fake_hyperfine_run(paths, data=sin_times),
    )

    assert hybrid.correr_hybrid("fork", FakeScxManager(), logs=False) is None
    assert paths and not paths[0].parent.exists()


def test_loaded_rejects_fewer_than_twenty_real_samples(monkeypatch):
    _habilitar_binarios(monkeypatch)
    load_process = FakeLoadProcess()
    data = {
        "results": [
            {
                "times": [value * 1e-6 for value in range(1, 20)],
                "mean": 10e-6,
                "stddev": 2e-6,
                "min": 1e-6,
                "max": 19e-6,
            }
        ]
    }
    monkeypatch.setattr(hybrid, "run_process", _fake_hyperfine_run([], data=data))
    monkeypatch.setattr(hybrid, "start_process", lambda *_args, **_kwargs: load_process)

    assert hybrid.correr_hybrid("loaded", FakeScxManager(), logs=False) is None
    assert load_process.terminated


def test_hybrid_cancellation_propagates_and_cleans_tempdir(monkeypatch):
    _habilitar_binarios(monkeypatch)
    token = NonBlockingCancellationToken()
    paths = []

    def cancelar(cmd, **kwargs):
        paths.append(Path(cmd[cmd.index("--export-json") + 1]))
        assert kwargs["cancel_token"] is token
        token.cancel()
        raise OperationCancelled("cancelado durante Hyperfine")

    monkeypatch.setattr(hybrid, "run_process", cancelar)

    with pytest.raises(OperationCancelled):
        hybrid.correr_hybrid(
            "fork",
            FakeScxManager(),
            logs=False,
            cancel_token=token,
        )

    assert paths and not paths[0].parent.exists()


def test_compile_cancellation_cleans_source_output_and_json(monkeypatch):
    _habilitar_binarios(monkeypatch)
    token = NonBlockingCancellationToken()
    artifacts = {}

    def cancelar(cmd, **kwargs):
        json_path = Path(cmd[cmd.index("--export-json") + 1])
        compilar = shlex.split(cmd[-1])
        source = Path(compilar[4])
        output = Path(compilar[compilar.index("-o") + 1])
        assert kwargs["cancel_token"] is token
        assert source.is_file()
        assert source.parent == output.parent == json_path.parent
        artifacts.update(json=json_path, source=source, output=output)
        token.cancel()
        raise OperationCancelled("cancelado durante compilacion")

    monkeypatch.setattr(hybrid, "run_process", cancelar)

    with pytest.raises(OperationCancelled):
        hybrid.correr_hybrid(
            "compile",
            FakeScxManager(),
            logs=False,
            cancel_token=token,
        )

    assert artifacts and not artifacts["json"].parent.exists()


@pytest.mark.parametrize("fallo", ["nonzero", "timeout"])
def test_hybrid_tempdir_is_removed_on_all_failures(monkeypatch, fallo):
    _habilitar_binarios(monkeypatch)
    paths = []

    def ejecutar(cmd, **kwargs):
        json_path = Path(cmd[cmd.index("--export-json") + 1])
        paths.append(json_path)
        if fallo == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return subprocess.CompletedProcess(cmd, 1, "", "fallo")

    monkeypatch.setattr(hybrid, "run_process", ejecutar)

    assert hybrid.correr_hybrid("fork", FakeScxManager(), logs=False) is None
    assert paths and not paths[0].parent.exists()


def test_loaded_timeout_cleans_only_its_load_process(monkeypatch):
    _habilitar_binarios(monkeypatch)
    load_process = FakeLoadProcess()
    commands = []

    def ejecutar(cmd, **kwargs):
        commands.append(cmd)
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(hybrid, "run_process", ejecutar)
    monkeypatch.setattr(hybrid, "start_process", lambda *_args, **_kwargs: load_process)

    assert hybrid.correr_hybrid("loaded", FakeScxManager(), logs=False) is None
    assert "pkill" not in " ".join(commands[0])
    assert load_process.waits
    assert load_process.terminated


def test_loaded_cancellation_propagates_and_cleans_load_process(monkeypatch):
    _habilitar_binarios(monkeypatch)
    token = NonBlockingCancellationToken()
    load_process = FakeLoadProcess()
    received_tokens = []
    paths = []

    def ejecutar(cmd, **kwargs):
        received_tokens.append(kwargs["cancel_token"])
        paths.append(Path(cmd[cmd.index("--export-json") + 1]))
        token.cancel()
        raise OperationCancelled("cancelado durante Hyperfine")

    monkeypatch.setattr(hybrid, "run_process", ejecutar)
    monkeypatch.setattr(hybrid, "start_process", lambda *_args, **_kwargs: load_process)

    with pytest.raises(OperationCancelled):
        hybrid.correr_hybrid(
            "loaded",
            FakeScxManager(),
            logs=False,
            cancel_token=token,
        )

    assert received_tokens == [token]
    assert paths and not paths[0].parent.exists()
    assert load_process.waits
    assert load_process.terminated


def test_loaded_fails_if_its_load_process_dies_during_measurement(monkeypatch):
    _habilitar_binarios(monkeypatch)
    load_process = DyingLoadProcess()
    monkeypatch.setattr(hybrid, "run_process", _fake_hyperfine_run([]))
    monkeypatch.setattr(hybrid, "start_process", lambda *_args, **_kwargs: load_process)

    assert hybrid.correr_hybrid("loaded", FakeScxManager(), logs=False) is None
    assert load_process.waits


def test_compile_generates_private_fixture_without_tmp_dependency(monkeypatch):
    _habilitar_binarios(monkeypatch)
    artifacts = {}

    def ejecutar(cmd, **_kwargs):
        json_path = Path(cmd[cmd.index("--export-json") + 1])
        preparar = shlex.split(cmd[cmd.index("--prepare") + 1])
        compilar = shlex.split(cmd[-1])
        fuente = Path(compilar[4])
        salida = Path(compilar[compilar.index("-o") + 1])

        assert compilar[:4] == ["/mock/bin/cc", "-std=c11", "-O2", "-pipe"]
        assert preparar == ["rm", "-f", str(salida)]
        assert fuente.parent == json_path.parent == salida.parent
        assert fuente.is_file()
        contenido = fuente.read_text(encoding="utf-8")
        assert contenido == hybrid._generar_fuente_compilacion()
        assert contenido.count("static uint64_t mix_") == hybrid._COMPILE_FUNCTIONS
        assert len(contenido) > 80_000
        assert "/tmp/rt-tests" not in " ".join(cmd)

        artifacts.update(json=json_path, source=fuente, output=salida)
        json_path.write_text(json.dumps(HYPERFINE_JSON), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(hybrid, "run_process", ejecutar)

    resultado = hybrid.correr_hybrid("compile", FakeScxManager(), logs=False)

    assert resultado["p95"] == pytest.approx(19.0)
    assert int(resultado["runs"]) >= 20
    assert artifacts and not artifacts["json"].parent.exists()


def test_compiler_resolver_requires_regular_executable(monkeypatch, tmp_path):
    directory = tmp_path / "cc"
    directory.mkdir()
    compiler = tmp_path / "gcc"
    compiler.write_text("compiler", encoding="utf-8")
    expected = hybrid.os.path.realpath(hybrid.os.path.abspath(str(compiler)))
    candidates = {"cc": str(directory), "gcc": str(compiler)}
    lookups = []

    def which(name):
        lookups.append(name)
        return candidates.get(name)

    monkeypatch.setattr(hybrid.shutil, "which", which)
    monkeypatch.setattr(
        hybrid.os,
        "access",
        lambda path, mode: path == expected and mode == hybrid.os.X_OK,
    )

    assert hybrid._resolver_compilador() == expected
    assert lookups == ["cc", "gcc"]

    monkeypatch.setattr(hybrid.os, "access", lambda _path, _mode: False)
    assert hybrid._resolver_compilador() is None


def test_compile_commands_quote_every_nested_path():
    compiler = "/opt/tool chain/cc's;safe"
    source = "/var/tmp/reactor work/source's;fixture.c"
    output = "/var/tmp/reactor work/output's;fixture"

    preparar, compilar = hybrid._comandos_compilacion(compiler, source, output)

    assert shlex.split(preparar) == ["rm", "-f", output]
    assert shlex.split(compilar) == [
        compiler,
        "-std=c11",
        "-O2",
        "-pipe",
        source,
        "-o",
        output,
    ]


def test_compile_missing_compiler_is_clear_and_does_not_start(monkeypatch):
    _habilitar_binarios(monkeypatch)
    mensajes = []
    monkeypatch.setattr(hybrid, "_resolver_compilador", lambda: None)
    monkeypatch.setattr(
        hybrid,
        "_log",
        lambda _tv, texto, *_args, **_kwargs: mensajes.append(texto),
    )

    def forbidden_run(*_args, **_kwargs):
        raise AssertionError("Hyperfine no debe arrancar sin compilador C")

    monkeypatch.setattr(hybrid, "run_process", forbidden_run)

    assert hybrid.correr_hybrid("compile", FakeScxManager(), object()) is None
    assert any("compilador C" in mensaje for mensaje in mensajes)
    assert any("cc/gcc/clang" in mensaje for mensaje in mensajes)


def test_missing_binary_messages_identify_the_actual_tool(monkeypatch):
    mensajes = []
    monkeypatch.setattr(
        benchmark,
        "_log",
        lambda _tv, texto, *_args, **_kwargs: mensajes.append(texto),
    )
    monkeypatch.setattr(
        hybrid,
        "_log",
        lambda _tv, texto, *_args, **_kwargs: mensajes.append(texto),
    )

    assert benchmark.correr_benchmark("cpu", MissingScxManager(), object()) is None
    assert any("scxctl" in mensaje for mensaje in mensajes)

    mensajes.clear()
    monkeypatch.setattr(hybrid.shutil, "which", lambda _name: None)
    assert hybrid.correr_hybrid("fork", FakeScxManager(), object()) is None
    assert any("hyperfine" in mensaje for mensaje in mensajes)


def test_dev_mode_is_stable_and_obeys_the_same_contract():
    stress_a = benchmark._resultado_dev("cpu", "scx_test", "auto")
    stress_b = benchmark._resultado_dev("cpu", "scx_test", "auto")
    stress_threads = benchmark._resultado_dev("threads", "scx_test", "auto")
    hybrid_a = hybrid._resultado_dev("fork", "scx_test", "auto")
    hybrid_b = hybrid._resultado_dev("fork", "scx_test", "auto")

    for clave in (
        "valor",
        "response",
        "response_kind",
        "p95",
        "fairness",
        "fairness_kind",
    ):
        assert stress_a[clave] == stress_b[clave]
        assert hybrid_a[clave] == hybrid_b[clave]
    assert stress_a["p95"] is None
    assert stress_a["fairness_kind"] == "cpu_idle_fraction"
    assert stress_threads["response"] is None
    assert stress_threads["response_kind"] is None
    assert stress_threads["p95"] is None
    assert stress_threads["valor"] > 0
    assert stress_threads["fairness"] >= 0
    assert hybrid_a["response"] == hybrid_a["p95"]
    assert hybrid_a["response_kind"] == "p95_us"
    assert hybrid_a["fairness_kind"] == "coefficient_of_variation"


def test_dev_mode_preserves_base_system_scheduler_in_both_engines():
    class BaseSystemScxManager:
        def capturar_estado(self, cancel_token=None):
            return ScxState()

    stress = benchmark.correr_benchmark(
        "cpu",
        BaseSystemScxManager(),
        logs=False,
        modo_dev=True,
    )
    latency = hybrid.correr_hybrid(
        "fork",
        BaseSystemScxManager(),
        logs=False,
        modo_dev=True,
    )

    assert stress["sched"] == "Sistema Base"
    assert latency["sched"] == "Sistema Base"


def test_dev_mode_is_deterministic_between_python_processes():
    codigo = """
import json
import sys
import types
helpers = types.ModuleType('utils.helpers')
helpers.log = lambda *args, **kwargs: None
helpers.limpiar_texto = lambda text: text or ''
sys.modules['utils.helpers'] = helpers
from core import benchmark, hybrid
stress = benchmark._resultado_dev('cpu', 'scx_test', 'auto')
latency = hybrid._resultado_dev('fork', 'scx_test', 'auto')
print(json.dumps([
    stress['valor'], stress['response'], stress['fairness'],
    latency['valor'], latency['response'], latency['fairness'],
]))
"""

    primera = subprocess.check_output([sys.executable, "-c", codigo], text=True)
    segunda = subprocess.check_output([sys.executable, "-c", codigo], text=True)
    assert json.loads(primera) == json.loads(segunda)
