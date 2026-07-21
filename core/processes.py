"""Ejecucion de procesos cancelable y sin procesos huerfanos."""

import math
import os
import signal
import subprocess
import time

from core.operations import OperationCancelled


_POLL_INTERVAL = 0.1
_STOP_TIMEOUT = 1.0
_POSIX_PROCESS_GROUPS = os.name == "posix" and hasattr(os, "killpg")
_SIGTERM = getattr(signal, "SIGTERM", 15)
_SIGKILL = getattr(signal, "SIGKILL", 9)


def _is_cancelled(cancel_token):
    if cancel_token is None:
        return False

    for attribute in ("cancelled", "cancel_requested"):
        value = getattr(cancel_token, attribute, False)
        if callable(value):
            value = value()
        if value:
            return True

    is_set = getattr(cancel_token, "is_set", None)
    return bool(is_set()) if callable(is_set) else False


def check_cancelled(cancel_token):
    """Propaga la cancelacion de un token o evento compatible."""
    if cancel_token is None:
        return

    raise_if_cancelled = getattr(cancel_token, "raise_if_cancelled", None)
    if callable(raise_if_cancelled):
        raise_if_cancelled()
    if _is_cancelled(cancel_token):
        raise OperationCancelled("La operacion fue cancelada.")


def wait_cancelable(cancel_token, seconds):
    """Espera sin bloquear una solicitud de cancelacion basada en Event."""
    seconds = max(0.0, float(seconds))
    check_cancelled(cancel_token)
    if seconds == 0:
        return

    wait = getattr(cancel_token, "wait", None) if cancel_token is not None else None
    if callable(wait):
        if wait(seconds):
            raise OperationCancelled("La operacion fue cancelada.")
        check_cancelled(cancel_token)
        return

    if cancel_token is None:
        time.sleep(seconds)
        return

    deadline = time.monotonic() + seconds
    while True:
        check_cancelled(cancel_token)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(_POLL_INTERVAL, remaining))
    check_cancelled(cancel_token)


def start_process(command, *, cancel_token=None, **popen_kwargs):
    """Inicia un proceso aislado en su propia sesion cuando POSIX lo permite."""
    check_cancelled(cancel_token)
    if _POSIX_PROCESS_GROUPS:
        popen_kwargs["start_new_session"] = True
    return subprocess.Popen(command, **popen_kwargs)


def _signal_process(process, sig):
    if _POSIX_PROCESS_GROUPS:
        try:
            os.killpg(process.pid, sig)
            return
        except ProcessLookupError:
            return
        except (PermissionError, OSError):
            pass

    try:
        if sig == _SIGTERM:
            process.terminate()
        else:
            process.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _communicate_or_wait(process, timeout):
    communicate = getattr(process, "communicate", None)
    if callable(communicate):
        return communicate(timeout=timeout)
    process.wait(timeout=timeout)
    return None, None


def terminate_process(process, timeout=_STOP_TIMEOUT):
    """Envia TERM y despues KILL con esperas acotadas; devuelve su salida."""
    if process is None:
        return None, None

    _signal_process(process, _SIGTERM)
    try:
        stdout, stderr = _communicate_or_wait(process, timeout)
        # El lider puede terminar mientras deja descendientes vivos. En POSIX,
        # un KILL final al grupo no afecta a nada si este ya desaparecio.
        if _POSIX_PROCESS_GROUPS:
            _signal_process(process, _SIGKILL)
        return stdout, stderr
    except subprocess.TimeoutExpired as term_timeout:
        stdout, stderr = term_timeout.output, term_timeout.stderr
    except (ProcessLookupError, OSError, ValueError):
        try:
            process.wait(timeout=timeout)
            return None, None
        except (subprocess.TimeoutExpired, ProcessLookupError, OSError, ValueError):
            stdout = stderr = None

    _signal_process(process, _SIGKILL)
    try:
        return _communicate_or_wait(process, timeout)
    except subprocess.TimeoutExpired as kill_timeout:
        return (
            kill_timeout.output if kill_timeout.output is not None else stdout,
            kill_timeout.stderr if kill_timeout.stderr is not None else stderr,
        )
    except (ProcessLookupError, OSError, ValueError):
        return stdout, stderr


def run_process(
    command,
    *,
    cancel_token=None,
    timeout=None,
    poll_interval=_POLL_INTERVAL,
    input=None,
    **popen_kwargs,
):
    """Ejecuta un comando con captura, timeout y cancelacion cooperativa."""
    if timeout is not None:
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("El timeout debe ser finito y no negativo")
    poll_interval = float(poll_interval)
    if not math.isfinite(poll_interval) or poll_interval <= 0:
        raise ValueError("El intervalo de polling debe ser positivo")

    popen_kwargs.setdefault("stdout", subprocess.PIPE)
    popen_kwargs.setdefault("stderr", subprocess.PIPE)
    popen_kwargs.setdefault("text", True)

    deadline = None if timeout is None else time.monotonic() + timeout
    process = start_process(
        command,
        cancel_token=cancel_token,
        **popen_kwargs,
    )
    pending_input = input
    completed_result = None

    try:
        while True:
            check_cancelled(cancel_token)
            wait_timeout = poll_interval
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                wait_timeout = min(wait_timeout, remaining)

            try:
                if pending_input is None:
                    stdout, stderr = process.communicate(timeout=wait_timeout)
                else:
                    current_input = pending_input
                    pending_input = None
                    stdout, stderr = process.communicate(
                        input=current_input,
                        timeout=wait_timeout,
                    )
            except subprocess.TimeoutExpired:
                continue

            completed_result = subprocess.CompletedProcess(
                command,
                process.returncode,
                stdout,
                stderr,
            )
            check_cancelled(cancel_token)
            return completed_result
    except OperationCancelled as exc:
        if completed_result is not None:
            exc.completed_process = completed_result
            raise
        try:
            terminate_process(process)
        except Exception:
            pass
        raise
    except subprocess.TimeoutExpired:
        stdout, stderr = terminate_process(process)
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout,
            stderr=stderr,
        )
    except BaseException:
        try:
            terminate_process(process)
        except Exception:
            pass
        raise
