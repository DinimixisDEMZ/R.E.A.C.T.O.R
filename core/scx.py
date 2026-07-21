"""Interacción acotada y restaurable con ``scxctl``.

Los nombres públicos históricos relacionados con sudo se conservan, pero su
semántica es la del backend privilegiado configurado: ``sudo``, ``run0`` o
ejecución directa cuando el proceso ya es root.
"""

from dataclasses import dataclass
import json
import math
import os
import posixpath
import re
import shutil
import stat
import subprocess
import threading
from typing import Optional

from core.operations import OperationCancelled
from core.processes import check_cancelled, run_process


TIMEOUT_RETURN_CODE = 124
OS_ERROR_RETURN_CODE = 126
NOT_FOUND_RETURN_CODE = 127
BASE_SYSTEM_NAME = "Sistema Base"
_PRIVILEGED_TIMEOUT_LAUNCH_GRACE = 1.0
_MAX_SYMLINKS = 40
_BACKEND_UNSET = object()

_RUNNING_STATE_RE = re.compile(
    r"^\s*running\s+([\w.-]+)(?:\s+(.*?))?\s*$",
    re.IGNORECASE,
)
_MODE_SUFFIX_RE = re.compile(
    r"(?:^|\s+)in\s+([\w.-]+)\s+mode\s*$",
    re.IGNORECASE,
)
_LEGACY_MODE_RE = re.compile(r"^[\[(]([\w.-]+)[\])]$", re.IGNORECASE)
_ARGUMENTS_STATE_RE = re.compile(r"^with\s+arguments(?:\s+.+)?$", re.IGNORECASE)
_STOPPED_STATE_RE = re.compile(
    r"^\s*(?:"
    r"stopped(?:\s+\(\s*sistema\s+base\s*\))?"
    r"|inactive"
    r"|not\s+running(?:\s+.*)?"
    r"|no\s+(?:scx\s+)?scheduler(?:\s+is)?(?:\s+currently)?\s+running"
    r"|sistema\s+base"
    r")\s*$",
    re.IGNORECASE,
)
_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")
_CLI_MODES = {
    "auto": "auto",
    "gaming": "gaming",
    "powersave": "powersave",
    "lowlatency": "lowlatency",
    "server": "server",
}


def _canonical_scheduler(value):
    if value is None:
        return None
    normalized = str(value).strip().casefold()
    return normalized or None


def _canonical_mode(value):
    normalized = "auto" if value is None else str(value).strip().casefold()
    if not normalized:
        normalized = "auto"
    mode_key = re.sub(r"[-_\s]+", "", normalized)
    return _CLI_MODES.get(mode_key, normalized)


class ScxCommandError(RuntimeError):
    """Un comando SCX necesario para una operación segura falló."""

    def __init__(self, message, result=None):
        self.result = result
        super().__init__(message)


class ScxRestorationError(ScxCommandError):
    """No se pudo restaurar el estado SCX solicitado."""


class _ExecutablePathError(RuntimeError):
    """Una ruta ejecutable no pudo resolverse o no es segura."""

    def __init__(self, message, returncode=OS_ERROR_RETURN_CODE):
        self.returncode = returncode
        super().__init__(message)


@dataclass(frozen=True)
class ScxState:
    """Estado SCX capturado; ``scheduler=None`` representa Sistema Base."""

    scheduler: Optional[str] = None
    mode: Optional[str] = None

    def __post_init__(self):
        scheduler = _canonical_scheduler(self.scheduler)
        mode = _canonical_mode(self.mode) if scheduler is not None else None
        object.__setattr__(self, "scheduler", scheduler)
        object.__setattr__(self, "mode", mode)

    @property
    def running(self):
        return self.scheduler is not None

    @property
    def sistema_base(self):
        return not self.running


class ScxManager:
    """Capa de abstracción segura para ``scxctl`` y privilegios."""

    SIMULATED_SCHEDULERS = (
        "scx_rusty",
        "scx_lavd",
        "scx_central",
        "scx_bpfland",
        "scx_ghost",
        "scx_dummy",
        "scx_mock",
    )
    PRIVILEGE_BACKENDS = frozenset(("auto", "sudo", "run0", "direct", "none"))

    def __init__(
        self,
        modo_desarrollador=False,
        *,
        backend_privilegios="auto",
        subprocess_runner=None,
        which=None,
        executable_validator=None,
        executable_probe=None,
        command_timeout=15.0,
        auth_timeout=10.0,
        process_stop_timeout=2.0,
    ):
        self._runner = subprocess_runner or self._default_subprocess_runner
        self._which = which or shutil.which
        self._executable_validator = (
            executable_validator or self._default_executable_validator
        )
        self._executable_probe = executable_probe or self._default_executable_probe
        self._resolved_executables = {}
        self._resolution_lock = threading.RLock()
        self._backend_errors = {}
        self.command_timeout = self._positive_timeout(
            command_timeout, "command_timeout"
        )
        self.auth_timeout = self._positive_timeout(auth_timeout, "auth_timeout")
        self.process_stop_timeout = self._positive_timeout(
            process_stop_timeout, "process_stop_timeout"
        )

        self._backend_configurado = self._normalize_backend(backend_privilegios)
        self._backend_activo = None
        self._state_lock = threading.RLock()
        self._state_revision = 0
        self._last_applied_state = None
        self._config_lock = threading.RLock()
        self._config_revision = 0
        self._session_local = threading.local()
        self._owned_processes = {}
        self._owned_processes_lock = threading.RLock()
        self._simulation_lock = threading.RLock()
        self._modo_desarrollador = False
        self._sim_sched = None
        self._sim_modo = None
        self.ultimo_error = None
        self.modo_desarrollador = modo_desarrollador

    @staticmethod
    def _positive_timeout(value, name):
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} debe ser finito y mayor que cero.")
        return value

    @classmethod
    def _normalize_backend(cls, backend):
        normalized = "auto" if backend is None else str(backend).lower().strip()
        if normalized == "ninguno":
            normalized = "none"
        if normalized not in cls.PRIVILEGE_BACKENDS:
            valid = ", ".join(sorted(cls.PRIVILEGE_BACKENDS))
            raise ValueError(f"Backend privilegiado inválido: {backend!r}. Use {valid}.")
        return normalized

    @property
    def modo_desarrollador(self):
        with self._config_lock:
            return self._modo_desarrollador

    @modo_desarrollador.setter
    def modo_desarrollador(self, enabled):
        enabled = bool(enabled)
        with self._config_lock:
            with self._simulation_lock:
                if enabled == self._modo_desarrollador:
                    return
                if enabled:
                    self._sim_sched = None
                    self._sim_modo = None
                self._modo_desarrollador = enabled
                self._config_revision += 1

    @staticmethod
    def _default_subprocess_runner(command, **kwargs):
        capture_output = bool(kwargs.pop("capture_output", False))
        text = bool(kwargs.pop("text", False))
        timeout = kwargs.pop("timeout", None)
        cancel_token = kwargs.pop("cancel_token", None)
        input_text = kwargs.pop("input", None)
        if capture_output:
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.PIPE
        else:
            kwargs.setdefault("stdout", None)
            kwargs.setdefault("stderr", None)
        if input_text is not None:
            kwargs["stdin"] = subprocess.PIPE
        kwargs["text"] = text
        return run_process(
            command,
            cancel_token=cancel_token,
            timeout=timeout,
            input=input_text,
            **kwargs,
        )

    @property
    def backend_privilegiado(self):
        """Backend activo, o el configurado si todavía no se ha seleccionado."""
        return self._backend_activo or self._backend_configurado

    def configurar_backend_privilegiado(self, backend):
        """Cambia explícitamente el backend y descarta la selección automática."""
        normalized = self._normalize_backend(backend)
        with self._config_lock:
            if normalized == self._backend_configurado:
                return
            self._backend_configurado = normalized
            self._backend_activo = None
            self._config_revision += 1

    def _error_result(self, args, returncode, message, stdout=""):
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=returncode,
            stdout=self._as_text(stdout),
            stderr=self._as_text(message),
        )

    @staticmethod
    def _as_text(value):
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode(errors="replace")
        return str(value)

    @staticmethod
    def _redact_text(value, redactions):
        text = ScxManager._as_text(value)
        for secret in redactions:
            secret = ScxManager._as_text(secret)
            if secret:
                text = text.replace(secret, "[REDACTADO]")
        return text

    @staticmethod
    def _append_error(existing, message):
        existing = ScxManager._as_text(existing).strip()
        return f"{existing}\n{message}".strip() if existing else message

    def _remember_result(self, result):
        if result.returncode == 0:
            error = None
        else:
            error = (
                self._as_text(getattr(result, "stderr", "")).strip()
                or self._as_text(getattr(result, "stdout", "")).strip()
                or f"código de salida {result.returncode}"
            )
        with self._state_lock:
            self.ultimo_error = error
        return result

    def _run_command(
        self,
        args,
        *,
        capture=True,
        timeout=None,
        input_text=None,
        redactions=(),
        cancel_token=None,
    ):
        check_cancelled(cancel_token)
        command = list(args)
        if not command:
            return self._error_result(command, 2, "No se especificó ningún comando.")

        effective_timeout = self.command_timeout if timeout is None else float(timeout)
        if not math.isfinite(effective_timeout) or effective_timeout <= 0:
            return self._error_result(
                command,
                2,
                "El timeout debe ser finito y mayor que cero.",
            )

        kwargs = {
            "capture_output": capture,
            "text": True,
            "timeout": effective_timeout,
        }
        if input_text is not None:
            kwargs["input"] = input_text
        if cancel_token is not None:
            kwargs["cancel_token"] = cancel_token

        try:
            result = self._runner(command, **kwargs)
        except OperationCancelled as exc:
            completed = getattr(exc, "completed_process", None)
            if completed is not None:
                exc.completed_process = subprocess.CompletedProcess(
                    args=command,
                    returncode=getattr(
                        completed,
                        "returncode",
                        OS_ERROR_RETURN_CODE,
                    ),
                    stdout=self._redact_text(
                        getattr(completed, "stdout", ""), redactions
                    ),
                    stderr=self._redact_text(
                        getattr(completed, "stderr", ""), redactions
                    ),
                )
            raise
        except subprocess.TimeoutExpired as exc:
            executable = os.path.basename(str(command[0]))
            message = (
                f"Tiempo de espera agotado tras {effective_timeout:g} s "
                f"al ejecutar '{executable}'."
            )
            return self._error_result(
                command,
                TIMEOUT_RETURN_CODE,
                self._append_error(
                    self._redact_text(exc.stderr, redactions),
                    message,
                ),
                stdout=self._redact_text(exc.stdout, redactions),
            )
        except FileNotFoundError:
            executable = str(command[0])
            return self._error_result(
                command,
                NOT_FOUND_RETURN_CODE,
                f"No se encontró el binario '{executable}'.",
            )
        except OSError as exc:
            executable = str(command[0])
            return self._error_result(
                command,
                OS_ERROR_RETURN_CODE,
                self._redact_text(
                    f"No se pudo ejecutar '{executable}': {exc}",
                    redactions,
                ),
            )

        completed = subprocess.CompletedProcess(
            args=command,
            returncode=getattr(result, "returncode", OS_ERROR_RETURN_CODE),
            stdout=self._redact_text(getattr(result, "stdout", ""), redactions),
            stderr=self._redact_text(getattr(result, "stderr", ""), redactions),
        )
        try:
            check_cancelled(cancel_token)
        except OperationCancelled as exc:
            exc.completed_process = completed
            raise
        return completed

    @staticmethod
    def _argument_value(args, *options):
        for index, argument in enumerate(args):
            if argument in options:
                return args[index + 1] if index + 1 < len(args) else None
            for option in options:
                prefix = f"{option}="
                if str(argument).startswith(prefix):
                    return str(argument)[len(prefix) :]
        return None

    @classmethod
    def _scx_action(cls, command):
        if len(command) < 2:
            return None
        executable = str(command[0]).replace("\\", "/").rsplit("/", 1)[-1]
        if executable != "scxctl":
            return None
        return str(command[1]).lower()

    @classmethod
    def _normalize_scx_command(cls, args):
        command = list(args)
        if cls._scx_action(command) not in ("start", "switch"):
            return command

        options = {
            "-s": _canonical_scheduler,
            "--sched": _canonical_scheduler,
            "-m": _canonical_mode,
            "--mode": _canonical_mode,
        }
        index = 2
        while index < len(command):
            argument = str(command[index])
            normalizer = options.get(argument)
            if normalizer is not None and index + 1 < len(command):
                normalized = normalizer(command[index + 1])
                command[index + 1] = "" if normalized is None else normalized
                index += 2
                continue
            for option, option_normalizer in options.items():
                prefix = f"{option}="
                if argument.startswith(prefix):
                    normalized = option_normalizer(argument[len(prefix) :])
                    command[index] = prefix + (
                        "" if normalized is None else normalized
                    )
                    break
            index += 1
        return command

    @classmethod
    def _applied_state_from_command(cls, command):
        action = cls._scx_action(command)
        if action == "stop":
            return ScxState()
        if action not in ("start", "switch"):
            return None

        scheduler = cls._argument_value(command, "-s", "--sched")
        if scheduler in (None, ""):
            return None
        mode = cls._argument_value(command, "-m", "--mode") or "auto"
        return ScxState(scheduler, mode)

    def _record_applied_state(self, command, result, cancel_token=None):
        action = self._scx_action(command)
        if result.returncode != 0 or action not in ("stop", "start", "switch"):
            return
        state = self._applied_state_from_command(command)
        if state is None and action in ("start", "switch"):
            try:
                state = self.parsear_estado(result.stdout)
            except ValueError:
                try:
                    state = self.capturar_estado(cancel_token=cancel_token)
                except ScxCommandError:
                    pass
            except ScxCommandError:
                pass
        with self._state_lock:
            self._state_revision += 1
            self._last_applied_state = state
            revision = self._state_revision
            session = getattr(self._session_local, "current", None)
            if session is not None and session._active:
                session._expected_state = state
                session._expected_revision = revision

    def _record_cancelled_completion(self, command, cancellation):
        completed = getattr(cancellation, "completed_process", None)
        if completed is not None:
            self._record_applied_state(command, completed, cancel_token=None)

    def _simulate(self, args):
        command = list(args)
        action = self._scx_action(command)

        with self._simulation_lock:
            if action == "list":
                return subprocess.CompletedProcess(
                    command, 0, json.dumps(list(self.SIMULATED_SCHEDULERS)), ""
                )

            if action == "get":
                if self._sim_sched is None:
                    output = "STOPPED (Sistema Base)"
                else:
                    output = f"RUNNING {self._sim_sched} in {self._sim_modo} mode"
                return subprocess.CompletedProcess(command, 0, output, "")

            if action == "stop":
                self._sim_sched = None
                self._sim_modo = None
                return subprocess.CompletedProcess(
                    command, 0, "STOPPED (Sistema Base)", ""
                )

            if action in ("start", "switch"):
                scheduler = self._argument_value(command, "-s", "--sched")
                mode = self._argument_value(command, "-m", "--mode") or "auto"
                if scheduler is None:
                    scheduler = self._sim_sched or self.SIMULATED_SCHEDULERS[0]
                self._sim_sched = scheduler
                self._sim_modo = mode
                return subprocess.CompletedProcess(
                    command,
                    0,
                    f"RUNNING {self._sim_sched} in {self._sim_modo} mode",
                    "",
                )

            return subprocess.CompletedProcess(command, 0, "OK (Simulated)", "")

    @staticmethod
    def _is_absolute_executable_path(path):
        try:
            path = os.fsdecode(os.fspath(path))
        except TypeError:
            return False
        return os.path.isabs(path) or path.startswith("/")

    def _resolve_executable(self, executable):
        """Resuelve una vez un nombre y conserva exactamente la ruta absoluta."""
        try:
            requested = os.fsdecode(os.fspath(executable))
        except TypeError as exc:
            raise _ExecutablePathError(
                f"Ejecutable inválido: {executable!r}."
            ) from exc
        if not requested:
            raise _ExecutablePathError("El ejecutable no puede estar vacío.")
        if self._is_absolute_executable_path(requested):
            return requested

        with self._resolution_lock:
            if requested not in self._resolved_executables:
                try:
                    resolved = self._which(requested)
                except Exception as exc:
                    cached = (
                        None,
                        f"No se pudo resolver el ejecutable '{requested}': {exc}",
                        OS_ERROR_RETURN_CODE,
                    )
                else:
                    if resolved is None:
                        cached = (
                            None,
                            f"No se encontró el ejecutable '{requested}' en PATH.",
                            NOT_FOUND_RETURN_CODE,
                        )
                    else:
                        try:
                            resolved = os.fsdecode(os.fspath(resolved))
                        except TypeError:
                            resolved = ""
                        if not self._is_absolute_executable_path(resolved):
                            cached = (
                                None,
                                "El resolver devolvió una ruta no absoluta para "
                                f"'{requested}': {resolved!r}.",
                                OS_ERROR_RETURN_CODE,
                            )
                        else:
                            cached = (resolved, None, 0)
                self._resolved_executables[requested] = cached

            resolved, error, returncode = self._resolved_executables[requested]

        if error is not None:
            raise _ExecutablePathError(error, returncode)
        return resolved

    @staticmethod
    def _validate_posix_executable_metadata(path, metadata):
        """Valida el target regular final de una ejecución como root."""
        mode = metadata.st_mode
        if stat.S_ISLNK(mode):
            raise ValueError("el target final sigue siendo un enlace simbólico")
        if not stat.S_ISREG(mode):
            raise ValueError("la ruta no es un archivo regular")
        if getattr(metadata, "st_uid", None) != 0:
            raise ValueError("el archivo no pertenece a root")
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError("el archivo es escribible por grupo u otros")
        if not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            raise ValueError("el archivo no tiene permisos de ejecución")
        return True

    @staticmethod
    def _validate_posix_directory_metadata(path, metadata):
        mode = metadata.st_mode
        if not stat.S_ISDIR(mode):
            raise ValueError(f"el componente '{path}' no es un directorio")
        if getattr(metadata, "st_uid", None) != 0:
            raise ValueError(f"el directorio '{path}' no pertenece a root")
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(
                f"el directorio '{path}' es escribible por grupo u otros"
            )
        return True

    @classmethod
    def _validate_posix_executable_path(
        cls,
        path,
        *,
        lstat=None,
        readlink=None,
    ):
        """Valida toda la cadena POSIX, incluidos symlinks y directorios."""
        lstat = lstat or os.lstat
        readlink = readlink or os.readlink
        try:
            requested = os.fsdecode(os.fspath(path))
        except TypeError as exc:
            raise ValueError(f"ruta inválida: {path!r}") from exc
        if not posixpath.isabs(requested):
            raise ValueError("la ruta no es absoluta")

        cls._validate_posix_directory_metadata("/", lstat("/"))
        pending = [part for part in requested.split("/") if part]
        resolved = []
        followed_links = 0

        while pending:
            component = pending.pop(0)
            if component == ".":
                continue
            if component == "..":
                if resolved:
                    resolved.pop()
                continue

            candidate = "/" + "/".join((*resolved, component))
            metadata = lstat(candidate)
            if stat.S_ISLNK(metadata.st_mode):
                if getattr(metadata, "st_uid", None) != 0:
                    raise ValueError(
                        f"el enlace simbólico '{candidate}' no pertenece a root"
                    )
                followed_links += 1
                if followed_links > _MAX_SYMLINKS:
                    raise ValueError("la cadena contiene demasiados enlaces simbólicos")
                target = os.fsdecode(os.fspath(readlink(candidate)))
                if not target:
                    raise ValueError(f"el enlace simbólico '{candidate}' está vacío")
                target_parts = [
                    part for part in target.split("/") if part not in ("", ".")
                ]
                if posixpath.isabs(target):
                    resolved = []
                pending = target_parts + pending
                continue

            if pending:
                cls._validate_posix_directory_metadata(candidate, metadata)
                resolved.append(component)
                continue

            cls._validate_posix_executable_metadata(candidate, metadata)
            return candidate

        raise ValueError("la ruta no resuelve a un archivo ejecutable")

    @classmethod
    def _default_executable_validator(cls, path):
        if os.name != "posix":
            return True
        try:
            cls._validate_posix_executable_path(path)
        except OSError as exc:
            raise ValueError(f"no se pudieron leer sus metadatos: {exc}") from exc
        return True

    @staticmethod
    def _default_executable_probe(path):
        if os.name != "posix":
            return False
        try:
            metadata = os.stat(path)
        except OSError:
            return False
        return stat.S_ISREG(metadata.st_mode) and bool(
            metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        )

    def _validate_privileged_executable(self, path):
        try:
            verdict = self._executable_validator(path)
        except _ExecutablePathError:
            raise
        except Exception as exc:
            raise _ExecutablePathError(
                f"Ejecutable privilegiado no confiable '{path}': {exc}"
            ) from exc
        if verdict is False:
            raise _ExecutablePathError(
                f"Ejecutable privilegiado no confiable '{path}'."
            )
        if isinstance(verdict, str) and verdict:
            raise _ExecutablePathError(
                f"Ejecutable privilegiado no confiable '{path}': {verdict}"
            )
        return path

    def _trusted_executable(self, executable):
        path = self._resolve_executable(executable)
        return self._validate_privileged_executable(path)

    @staticmethod
    def _timeout_command_index(command):
        executable = str(command[0]).replace("\\", "/").rsplit("/", 1)[-1]
        if executable != "timeout":
            return None

        options_with_value = {"-k", "--kill-after", "-s", "--signal"}
        index = 1
        while index < len(command):
            argument = str(command[index])
            if argument == "--":
                index += 2  # Fin de opciones y duración.
                break
            if argument in options_with_value:
                index += 2
                continue
            if argument.startswith(("--kill-after=", "--signal=")):
                index += 1
                continue
            if argument.startswith("-k") and argument != "-k":
                index += 1
                continue
            if argument.startswith("-s") and argument != "-s":
                index += 1
                continue
            if argument.startswith("-"):
                index += 1
                continue
            index += 1  # Duración.
            break
        return index if index < len(command) else None

    def _prepare_privileged_command(self, args):
        command = list(args)
        command[0] = self._trusted_executable(command[0])

        nested_executables = set()
        timeout_command = self._timeout_command_index(command)
        if timeout_command is not None:
            command[timeout_command] = self._trusted_executable(
                command[timeout_command]
            )
            nested_executables.add(timeout_command)

        for index, argument in enumerate(command[1:], start=1):
            if index in nested_executables:
                continue
            if not self._is_absolute_executable_path(argument):
                continue
            path = os.fsdecode(os.fspath(argument))
            try:
                is_executable = bool(self._executable_probe(path))
            except Exception as exc:
                raise _ExecutablePathError(
                    f"No se pudo inspeccionar el argumento absoluto '{path}': {exc}"
                ) from exc
            if is_executable:
                self._validate_privileged_executable(path)
        return command

    @staticmethod
    def _format_timeout_duration(value):
        return f"{float(value):.12g}s"

    def _wrap_privileged_timeout(self, command, timeout):
        timeout_path = self._trusted_executable("timeout")
        return [
            timeout_path,
            "--signal=TERM",
            "--kill-after=" + self._format_timeout_duration(
                self.process_stop_timeout
            ),
            self._format_timeout_duration(timeout),
            *command,
        ]

    def _outer_privileged_timeout(self, timeout):
        return (
            timeout
            + self.process_stop_timeout
            + self.auth_timeout
            + _PRIVILEGED_TIMEOUT_LAUNCH_GRACE
        )

    def scx_run(self, args, capture=True, timeout=None, cancel_token=None):
        """Ejecuta un comando acotado; en desarrollo lo simula sin procesos."""
        check_cancelled(cancel_token)
        command = self._normalize_scx_command(args)
        with self._state_lock:
            check_cancelled(cancel_token)
            if self.modo_desarrollador:
                result = self._simulate(command)
            elif not command:
                result = self._error_result(
                    command, 2, "No se especificó ningún comando."
                )
            else:
                try:
                    resolved_command = list(command)
                    resolved_command[0] = self._resolve_executable(command[0])
                except _ExecutablePathError as exc:
                    result = self._error_result(command, exc.returncode, str(exc))
                else:
                    try:
                        result = self._run_command(
                            resolved_command,
                            capture=capture,
                            timeout=timeout,
                            cancel_token=cancel_token,
                        )
                    except OperationCancelled as exc:
                        self._record_cancelled_completion(command, exc)
                        raise
            self._record_applied_state(command, result, cancel_token=None)
            check_cancelled(cancel_token)
            return self._remember_result(result)

    @staticmethod
    def _running_as_root():
        geteuid = getattr(os, "geteuid", None)
        if geteuid is None:
            return False
        try:
            return geteuid() == 0
        except OSError:
            return False

    def _backend_available(self, backend):
        if backend == "direct":
            return self._running_as_root() or self._backend_configurado == "direct"
        if backend in ("sudo", "run0"):
            try:
                self._trusted_executable(backend)
            except _ExecutablePathError as exc:
                self._backend_errors[backend] = str(exc)
                return False
            self._backend_errors.pop(backend, None)
            return True
        return False

    def _backend_error(self, fallback):
        if not self._backend_errors:
            return fallback
        return f"{fallback} " + "; ".join(self._backend_errors.values())

    def _select_execution_backend(self):
        configured = self._backend_configurado
        if configured != "auto":
            return None if configured == "none" else configured

        if self._backend_activo and self._backend_available(self._backend_activo):
            return self._backend_activo
        if self._running_as_root():
            self._backend_activo = "direct"
        elif self._backend_available("run0"):
            self._backend_activo = "run0"
        elif self._backend_available("sudo"):
            self._backend_activo = "sudo"
        else:
            self._backend_activo = None
        return self._backend_activo

    def _ejecutar_con_sudo_locked(
        self,
        command,
        *,
        timeout,
        cancel_token,
        modo_desarrollador,
        backend=_BACKEND_UNSET,
    ):
        """Ejecuta un comando normalizado con configuración ya capturada."""
        check_cancelled(cancel_token)
        if modo_desarrollador:
            result = self._simulate(command)
        elif not command:
            result = self._error_result(
                command, 2, "No se especificó ningún comando."
            )
        else:
            if backend is _BACKEND_UNSET:
                backend = self._select_execution_backend()
            if backend is None:
                result = self._error_result(
                    command,
                    NOT_FOUND_RETURN_CODE,
                    self._backend_error(
                        "No hay un backend privilegiado disponible (sudo/run0)."
                    ),
                )
            else:
                try:
                    backend_path = (
                        self._trusted_executable(backend)
                        if backend in ("sudo", "run0")
                        else None
                    )
                    resolved_command = self._prepare_privileged_command(command)
                    effective_timeout = (
                        self.command_timeout
                        if timeout is None
                        else float(timeout)
                    )
                    if (
                        not math.isfinite(effective_timeout)
                        or effective_timeout <= 0
                    ):
                        raise _ExecutablePathError(
                            "El timeout debe ser finito y mayor que cero.",
                            returncode=2,
                        )
                    if backend in ("sudo", "run0"):
                        bounded_command = self._wrap_privileged_timeout(
                            resolved_command,
                            effective_timeout,
                        )
                        runner_timeout = self._outer_privileged_timeout(
                            effective_timeout
                        )
                    else:
                        bounded_command = resolved_command
                        runner_timeout = effective_timeout
                except _ExecutablePathError as exc:
                    result = self._error_result(
                        command, exc.returncode, str(exc)
                    )
                else:
                    try:
                        if backend == "sudo":
                            result = self._run_command(
                                [backend_path, "-n", "--"] + bounded_command,
                                timeout=runner_timeout,
                                cancel_token=cancel_token,
                            )
                            if (
                                result.returncode != 0
                                and not result.stderr.strip()
                                and not result.stdout.strip()
                            ):
                                result.stderr = (
                                    "Autenticación sudo requerida o sesión expirada."
                                )
                        elif backend == "run0":
                            result = self._run_command(
                                [backend_path, "--"] + bounded_command,
                                timeout=runner_timeout,
                                cancel_token=cancel_token,
                            )
                        else:
                            result = self._run_command(
                                bounded_command,
                                timeout=runner_timeout,
                                cancel_token=cancel_token,
                            )
                    except OperationCancelled as exc:
                        self._record_cancelled_completion(command, exc)
                        raise

        self._record_applied_state(command, result, cancel_token=None)
        check_cancelled(cancel_token)
        return self._remember_result(result)

    def ejecutar_con_sudo(self, cmd_list, timeout=None, cancel_token=None):
        """Ejecuta mediante el backend privilegiado configurado.

        El nombre se conserva por compatibilidad. ``sudo`` siempre usa ``-n``
        para no bloquear esperando entrada; ``run0`` queda acotado por timeout.
        """
        check_cancelled(cancel_token)
        command = self._normalize_scx_command(cmd_list)
        with self._state_lock:
            check_cancelled(cancel_token)
            with self._config_lock:
                modo_desarrollador = self._modo_desarrollador
            return self._ejecutar_con_sudo_locked(
                command,
                timeout=timeout,
                cancel_token=cancel_token,
                modo_desarrollador=modo_desarrollador,
            )

    def registrar_proceso_propietario(self, process):
        """Registra un handle de proceso creado por Reactor para poder pararlo."""
        pid = getattr(process, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError("El proceso propietario debe exponer un PID válido.")
        for method in ("poll", "terminate", "kill", "wait"):
            if not callable(getattr(process, method, None)):
                raise TypeError(f"El proceso propietario no implementa {method}().")
        with self._owned_processes_lock:
            self._owned_processes[pid] = process
        return process

    # Alias corto para integraciones que ya poseen un objeto subprocess.Popen.
    registrar_proceso = registrar_proceso_propietario

    def olvidar_proceso_propietario(self, process_or_pid):
        pid = getattr(process_or_pid, "pid", process_or_pid)
        with self._owned_processes_lock:
            return self._owned_processes.pop(pid, None) is not None

    @property
    def pids_propios(self):
        with self._owned_processes_lock:
            return tuple(sorted(self._owned_processes))

    def _stop_owned_processes(self):
        with self._owned_processes_lock:
            owned = list(self._owned_processes.items())

        errors = []
        for pid, process in owned:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=self.process_stop_timeout)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=self.process_stop_timeout)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"PID {pid}: {exc}")
            finally:
                try:
                    finished = process.poll() is not None
                except OSError:
                    finished = False
                if finished:
                    self.olvidar_proceso_propietario(pid)
        return errors

    def _detener_todos_locked(
        self,
        *,
        cancel_token,
        modo_desarrollador,
        backend=_BACKEND_UNSET,
    ):
        result = self._ejecutar_con_sudo_locked(
            ["scxctl", "stop"],
            timeout=None,
            cancel_token=cancel_token,
            modo_desarrollador=modo_desarrollador,
            backend=backend,
        )
        process_errors = self._stop_owned_processes()
        if not process_errors:
            return result

        stderr = self._append_error(
            result.stderr,
            "No se pudieron detener procesos propios: " + "; ".join(process_errors),
        )
        return self._remember_result(
            subprocess.CompletedProcess(
                result.args,
                result.returncode or 1,
                result.stdout,
                stderr,
            )
        )

    def detener_todos(self, cancel_token=None):
        """Detiene SCX y solo los procesos registrados como propiedad de Reactor."""
        check_cancelled(cancel_token)
        with self._state_lock:
            check_cancelled(cancel_token)
            with self._config_lock:
                modo_desarrollador = self._modo_desarrollador
            return self._detener_todos_locked(
                cancel_token=cancel_token,
                modo_desarrollador=modo_desarrollador,
            )

    @staticmethod
    def parsear_estado(stdout):
        """Interpreta una salida reconocida de ``scxctl get``.

        Las líneas informativas se ignoran, pero debe existir un único estado
        coherente. Una salida desconocida o contradictoria se rechaza.
        """
        text = ScxManager._as_text(stdout).strip()
        states = []
        for line in text.splitlines():
            running_match = _RUNNING_STATE_RE.fullmatch(line)
            if running_match is not None:
                details = (running_match.group(2) or "").strip()
                mode = "auto"
                mode_match = _MODE_SUFFIX_RE.search(details)
                if mode_match is not None:
                    mode = mode_match.group(1)
                    details = details[: mode_match.start()].strip()
                elif details:
                    legacy_mode = _LEGACY_MODE_RE.fullmatch(details)
                    if legacy_mode is not None:
                        mode = legacy_mode.group(1)
                        details = ""

                if not details or _ARGUMENTS_STATE_RE.fullmatch(details):
                    states.append(ScxState(running_match.group(1), mode))
            elif _STOPPED_STATE_RE.fullmatch(line) is not None:
                states.append(ScxState())

        if not states:
            raise ValueError("Salida de estado SCX no reconocida.")
        state = states[0]
        if any(candidate != state for candidate in states[1:]):
            raise ValueError("Salida de estado SCX contradictoria.")
        return state

    @staticmethod
    def _result_message(action, result):
        detail = result.stderr.strip() or result.stdout.strip()
        if not detail:
            detail = f"código de salida {result.returncode}"
        return f"No se pudo {action}: {detail}"

    def capturar_estado(self, cancel_token=None):
        """Captura estrictamente el estado o lanza :class:`ScxCommandError`."""
        check_cancelled(cancel_token)
        with self._state_lock:
            check_cancelled(cancel_token)
            result = self.scx_run(
                ["scxctl", "get"],
                cancel_token=cancel_token,
            )
            if result.returncode != 0:
                raise ScxCommandError(
                    self._result_message("obtener el estado SCX", result),
                    result,
                )
            try:
                return self.parsear_estado(result.stdout)
            except ValueError as exc:
                self.ultimo_error = f"scxctl get devolvió un estado no reconocido: {exc}"
                raise ScxCommandError(self.ultimo_error, result) from exc

    def obtener_estado(self, cancel_token=None):
        """Devuelve ``(scheduler, modo)`` o lanza un error de consulta claro.

        Sistema Base se representa con ``(None, None)`` únicamente cuando la
        salida satisfactoria de ``scxctl get`` indica que SCX está detenido.
        """
        state = self.capturar_estado(cancel_token=cancel_token)
        return state.scheduler, state.mode

    def obtener_lista(self, compatibles=None, cancel_token=None):
        """Obtiene schedulers, sin parsear la salida si ``scxctl`` falló."""
        result = self.scx_run(
            ["scxctl", "list"],
            cancel_token=cancel_token,
        )
        if result.returncode != 0:
            return []

        try:
            match_json = _JSON_ARRAY_RE.search(result.stdout)
            if match_json is None:
                self.ultimo_error = "scxctl list no devolvió una lista JSON."
                return []
            decoded = json.loads(match_json.group())
            if not isinstance(decoded, list):
                self.ultimo_error = "scxctl list devolvió un JSON no válido."
                return []
            names = []
            seen = set()
            for name in decoded:
                if not isinstance(name, str):
                    continue
                canonical = _canonical_scheduler(name)
                if canonical is None or canonical in seen:
                    continue
                seen.add(canonical)
                names.append(canonical)
        except json.JSONDecodeError as exc:
            self.ultimo_error = f"Salida JSON inválida de scxctl list: {exc}"
            return []

        self.ultimo_error = None
        if compatibles is not None:
            compatibles = {
                canonical
                for name in compatibles
                if (canonical := _canonical_scheduler(name)) is not None
            }
            return [name for name in names if name in compatibles]
        return names

    def sudo_disponible(self):
        """Comprueba si el backend privilegiado puede usarse sin bloquear.

        Para sudo comprueba una sesión no interactiva. Para run0 basta con que
        exista el binario: su autorización se gestiona al ejecutar el comando.
        """
        if self.modo_desarrollador:
            self.ultimo_error = None
            return True

        configured = self._backend_configurado
        if configured == "none":
            self.ultimo_error = "El backend privilegiado está deshabilitado."
            return False
        if configured == "direct":
            self._backend_activo = "direct"
            self.ultimo_error = None
            return True
        if configured == "run0":
            try:
                self._trusted_executable("run0")
            except _ExecutablePathError as exc:
                self._backend_activo = None
                self.ultimo_error = str(exc)
                return False
            self._backend_activo = "run0"
            self.ultimo_error = None
            return True

        if configured == "auto" and self._running_as_root():
            self._backend_activo = "direct"
            self.ultimo_error = None
            return True

        sudo_error = None
        if configured in ("auto", "sudo"):
            try:
                sudo_path = self._trusted_executable("sudo")
            except _ExecutablePathError as exc:
                sudo_error = str(exc)
            else:
                result = self._run_command(
                    [sudo_path, "-n", "-v"],
                    timeout=self.auth_timeout,
                )
                if result.returncode == 0:
                    self._backend_activo = "sudo"
                    self.ultimo_error = None
                    return True
                sudo_error = (
                    result.stderr.strip() or "La sesión sudo no está activa."
                )

        if configured == "auto":
            try:
                self._trusted_executable("run0")
            except _ExecutablePathError as exc:
                self._backend_errors["run0"] = str(exc)
            else:
                self._backend_activo = "run0"
                self.ultimo_error = None
                return True

        self._backend_activo = None
        self.ultimo_error = sudo_error or self._backend_error(
            "No hay un backend privilegiado disponible."
        )
        return False

    def validar_sudo(self, pwd):
        """Valida o prepara el backend privilegiado.

        Con sudo valida ``pwd`` de forma acotada. run0 no consume contraseñas de
        la aplicación y delega la autorización a su agente del sistema.
        """
        if self.modo_desarrollador:
            self.ultimo_error = None
            return True

        configured = self._backend_configurado
        backend = self._backend_activo
        if backend is None:
            if configured == "auto":
                if self._backend_available("sudo"):
                    backend = "sudo"
                elif self._backend_available("run0"):
                    backend = "run0"
                elif self._running_as_root():
                    backend = "direct"
            elif configured != "none":
                backend = configured

        if backend == "run0":
            try:
                self._trusted_executable("run0")
            except _ExecutablePathError as exc:
                self._backend_activo = None
                self.ultimo_error = str(exc)
                return False
            self._backend_activo = "run0"
            self.ultimo_error = None
            return True
        if backend == "direct":
            self._backend_activo = "direct"
            self.ultimo_error = None
            return True
        if backend != "sudo":
            self.ultimo_error = self._backend_error(
                "No hay un backend privilegiado disponible."
            )
            return False

        try:
            sudo_path = self._trusted_executable("sudo")
        except _ExecutablePathError as exc:
            self._backend_activo = None
            self.ultimo_error = str(exc)
            return False

        secret = self._as_text(pwd)
        password_input = f"{secret}\n"
        try:
            result = self._run_command(
                [sudo_path, "-S", "-v"],
                timeout=self.auth_timeout,
                input_text=password_input,
                redactions=(secret,),
            )
        finally:
            pwd = None
            password_input = None
            secret = None

        if result.returncode == 0:
            self._backend_activo = "sudo"
            self.ultimo_error = None
            return True
        self.ultimo_error = result.stderr.strip() or "Falló la autenticación sudo."
        return False

    @staticmethod
    def _benign_stop_failure(result):
        if result.returncode == 0:
            return True
        if result.returncode != 1:
            return False
        lines = [
            line.strip().lower().rstrip(".")
            for line in f"{result.stdout}\n{result.stderr}".splitlines()
            if line.strip()
        ]
        if len(lines) != 1:
            return False
        normalized = re.sub(
            r"^(?:error|warning|scxctl)\s*:\s*",
            "",
            lines[0],
        )
        return bool(
            re.fullmatch(
                r"(?:(?:scx|scheduler|scx scheduler)\s+)?"
                r"(?:is\s+)?(?:not running|inactive|detenido)"
                r"|no\s+(?:scx\s+)?scheduler(?:\s+is)?(?:\s+currently)?\s+running",
                normalized,
            )
        )

    def _capturar_estado_con_revision(self, cancel_token=None):
        check_cancelled(cancel_token)
        with self._state_lock:
            check_cancelled(cancel_token)
            with self._config_lock:
                config_revision = self._config_revision
            state = self.capturar_estado(cancel_token=cancel_token)
            with self._config_lock:
                if self._config_revision != config_revision:
                    message = (
                        "La configuración SCX cambió durante la captura de estado; "
                        "se canceló la sesión para no sobrescribir ese cambio."
                    )
                    self.ultimo_error = message
                    raise ScxRestorationError(message)
            return state, self._state_revision, config_revision

    @staticmethod
    def _describir_estado(state):
        if not state.running:
            return BASE_SYSTEM_NAME
        return f"{state.scheduler} ({state.mode or 'auto'})"

    def _restaurar_si_estado_coincide(
        self,
        expected_state,
        expected_revision,
        expected_config_revision,
        target_state,
    ):
        with self._state_lock:
            with self._config_lock:
                current_config_revision = self._config_revision
            if current_config_revision != expected_config_revision:
                message = (
                    "La configuración SCX cambió fuera de esta sesión. "
                    "Se omitió la restauración para no sobrescribir el cambio externo."
                )
                self.ultimo_error = message
                raise ScxRestorationError(message)

            revision_changed = self._state_revision != expected_revision

            current_state = self.capturar_estado()
            with self._config_lock:
                config_changed = (
                    self._config_revision != current_config_revision
                )
            if config_changed:
                message = (
                    "La configuración SCX cambió fuera de esta sesión. "
                    "Se omitió la restauración para no sobrescribir el cambio externo."
                )
                self.ultimo_error = message
                raise ScxRestorationError(message)

            if (
                expected_state is None
                or current_state != expected_state
                or revision_changed
            ):
                expected = (
                    self._describir_estado(expected_state)
                    if expected_state is not None
                    else "un estado aplicado no determinable"
                )
                message = (
                    "El estado SCX cambió fuera de esta sesión: "
                    f"se esperaba {expected}, pero se encontró "
                    f"{self._describir_estado(current_state)}. "
                    "Se omitió la restauración para no sobrescribir el cambio externo."
                )
                self.ultimo_error = message
                raise ScxRestorationError(message)

            return self.restaurar_estado(target_state)

    def restaurar_estado(self, state, cancel_token=None):
        """Aplica un :class:`ScxState` de forma determinista o lanza error."""
        if not isinstance(state, ScxState):
            raise TypeError("state debe ser una instancia de ScxState.")

        check_cancelled(cancel_token)
        with self._state_lock:
            check_cancelled(cancel_token)
            with self._config_lock:
                modo_desarrollador = self._modo_desarrollador
                config_revision = self._config_revision
            backend = (
                _BACKEND_UNSET
                if modo_desarrollador
                else self._select_execution_backend()
            )
            stop_result = self._detener_todos_locked(
                cancel_token=cancel_token,
                modo_desarrollador=modo_desarrollador,
                backend=backend,
            )
            if not self._benign_stop_failure(stop_result):
                raise ScxRestorationError(
                    self._result_message("detener SCX para restaurarlo", stop_result),
                    stop_result,
                )
            if not state.running:
                with self._config_lock:
                    config_changed = self._config_revision != config_revision
                if config_changed:
                    raise ScxRestorationError(
                        "La configuración SCX cambió durante la transición; "
                        "el estado se aplicó con la configuración capturada."
                    )
                return stop_result

            start_result = self._ejecutar_con_sudo_locked(
                [
                    "scxctl",
                    "start",
                    "-s",
                    state.scheduler,
                    "-m",
                    state.mode or "auto",
                ],
                timeout=None,
                cancel_token=cancel_token,
                modo_desarrollador=modo_desarrollador,
                backend=backend,
            )
            if start_result.returncode != 0:
                raise ScxRestorationError(
                    self._result_message(
                        f"restaurar {state.scheduler} en modo {state.mode or 'auto'}",
                        start_result,
                    ),
                    start_result,
                )
            with self._config_lock:
                config_changed = self._config_revision != config_revision
            if config_changed:
                raise ScxRestorationError(
                    "La configuración SCX cambió durante la transición; "
                    "el estado se aplicó con la configuración capturada."
                )
            return start_result

    def sesion(self, token_cancelacion=None):
        """Crea una sesión que restaura el estado SCX al abandonar el bloque."""
        return ScxSession(self, token_cancelacion=token_cancelacion)


class ScxSession:
    """Contexto transaccional de estado SCX.

    Por defecto restaura el estado inicial. Un ganador solo se conserva tras
    ``conservar_ganador`` y si el bloque termina sin error ni cancelación.
    """

    def __init__(self, manager, token_cancelacion=None):
        self.manager = manager
        self.token_cancelacion = token_cancelacion
        self.initial_state = None
        self.restore_error = None
        self._winner_state = None
        self._initial_revision = None
        self._expected_state = None
        self._expected_revision = None
        self._config_revision = None
        self._entered = False
        self._active = False

    def __enter__(self):
        if self._entered:
            raise RuntimeError("La sesión SCX no puede reutilizarse.")
        active_session = getattr(self.manager._session_local, "current", None)
        if active_session is not None and active_session._active:
            raise RuntimeError("Ya existe una sesión SCX activa en este hilo.")
        self.comprobar_cancelacion()
        (
            self.initial_state,
            self._initial_revision,
            self._config_revision,
        ) = self.manager._capturar_estado_con_revision(
            cancel_token=self.token_cancelacion
        )
        self._expected_state = self.initial_state
        self._expected_revision = self._initial_revision
        self._entered = True
        self.comprobar_cancelacion()
        self._active = True
        self.manager._session_local.current = self
        return self

    def comprobar_cancelacion(self):
        check_cancelled(self.token_cancelacion)

    def aplicar(self, estado):
        """Aplica un estado solo si la sesión todavía posee el estado actual."""
        if not self._active:
            raise RuntimeError("La sesión SCX debe estar activa para aplicar estados.")
        if not isinstance(estado, ScxState):
            raise TypeError("estado debe ser una instancia de ScxState.")

        self.comprobar_cancelacion()
        manager = self.manager
        with manager._state_lock:
            self.comprobar_cancelacion()
            with manager._config_lock:
                config_revision = manager._config_revision
            current_state = manager.capturar_estado(
                cancel_token=self.token_cancelacion
            )
            with manager._config_lock:
                config_still_current = (
                    manager._config_revision == config_revision
                    == self._config_revision
                )

            revision_matches = manager._state_revision == self._expected_revision
            if (
                not config_still_current
                or not revision_matches
                or current_state != self._expected_state
            ):
                expected = manager._describir_estado(self._expected_state)
                message = (
                    "El estado SCX cambió fuera de esta sesión: "
                    f"se esperaba {expected}, pero se encontró "
                    f"{manager._describir_estado(current_state)}. "
                    "Se omitió la aplicación para no sobrescribir el cambio externo."
                )
                manager.ultimo_error = message
                raise ScxRestorationError(message)

            self.comprobar_cancelacion()
            result = manager.restaurar_estado(
                estado,
                cancel_token=self.token_cancelacion,
            )
            self._expected_state = estado
            self._expected_revision = manager._state_revision
            self._config_revision = config_revision
            return result

    def conservar_ganador(self, scheduler, mode="auto"):
        """Conserva explícitamente el ganador tras una salida satisfactoria."""
        if scheduler in (None, "", BASE_SYSTEM_NAME):
            self._winner_state = ScxState()
        else:
            self._winner_state = ScxState(str(scheduler), str(mode or "auto"))
        return self._winner_state

    @staticmethod
    def _cancel_requested(token):
        if token is None:
            return False
        cancelled = getattr(token, "cancelled", False)
        return bool(cancelled() if callable(cancelled) else cancelled)

    def __exit__(self, exc_type, exc_value, traceback):
        self._active = False
        if getattr(self.manager._session_local, "current", None) is self:
            del self.manager._session_local.current
        keep_winner = exc_type is None and self._winner_state is not None
        if keep_winner and self.token_cancelacion is not None:
            seal = getattr(self.token_cancelacion, "seal", None)
            if callable(seal):
                keep_winner = seal()
            else:
                # Sin una primitiva atómica no es seguro conservar el ganador.
                keep_winner = False

        if keep_winner:
            target_state = self._winner_state
        else:
            target_state = self.initial_state

        try:
            self.manager._restaurar_si_estado_coincide(
                self._expected_state,
                self._expected_revision,
                self._config_revision,
                target_state,
            )
        except BaseException as restore_error:
            self.restore_error = restore_error
            if exc_value is not None:
                message = (
                    "Falló la restauración SCX "
                    f"({type(restore_error).__name__}): {restore_error}. "
                    f"Error original ({type(exc_value).__name__}): {exc_value}"
                )
                raise ScxRestorationError(
                    message,
                    getattr(restore_error, "result", None),
                ) from exc_value
            raise
        return False
