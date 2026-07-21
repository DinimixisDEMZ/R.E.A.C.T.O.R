"""Coordinación global de operaciones largas de R.E.A.C.T.O.R.

La cancelación es cooperativa: el trabajo activo debe consultar su token en
puntos seguros. El coordinador garantiza que solo exista una operación activa,
pero no termina hilos de forma forzada.
"""

from dataclasses import dataclass
import threading
import time


class OperationBusyError(RuntimeError):
    """Se intentó iniciar una operación mientras otra seguía activa."""

    def __init__(self, active_state):
        self.active_state = active_state
        super().__init__(
            f"La operación '{active_state.name}' ya está en curso."
        )


class OperationCancelled(RuntimeError):
    """La operación recibió una solicitud de cancelación cooperativa."""


class CancellationToken:
    """Token thread-safe que comunica una solicitud de cancelación."""

    def __init__(self):
        self._event = threading.Event()
        self._state_lock = threading.Lock()
        self._sealed = False

    @property
    def cancelled(self):
        return self._event.is_set()

    @property
    def cancel_requested(self):
        return self.cancelled

    @property
    def accepting_cancellation(self):
        with self._state_lock:
            return not self._sealed and not self._event.is_set()

    def snapshot(self):
        """Devuelve atómicamente ``(cancelled, accepting_cancellation)``."""
        with self._state_lock:
            cancelled = self._event.is_set()
            return cancelled, not self._sealed and not cancelled

    def cancel(self):
        """Solicita cancelación y devuelve si esta es la primera solicitud."""
        with self._state_lock:
            if self._sealed or self._event.is_set():
                return False
            self._event.set()
            return True

    def seal(self):
        """Cierra atómicamente la ventana de cancelación antes de finalizar.

        Devuelve ``False`` si una cancelación ya ganó la carrera. Tras devolver
        ``True``, futuras llamadas a :meth:`cancel` se rechazan.
        """
        with self._state_lock:
            if self._event.is_set():
                return False
            self._sealed = True
            return True

    def wait(self, timeout=None):
        """Espera hasta la cancelación o hasta agotar ``timeout``."""
        return self._event.wait(timeout)

    def raise_if_cancelled(self):
        """Interrumpe cooperativamente el trabajo en un punto seguro."""
        if self.cancelled:
            raise OperationCancelled("La operación fue cancelada.")


@dataclass(frozen=True)
class OperationState:
    """Instantánea inmutable de la operación activa."""

    operation_id: int
    name: str
    started_at: float
    owner_thread_id: int
    cancel_requested: bool
    accepting_cancellation: bool = True

    @property
    def status(self):
        if self.cancel_requested:
            return "cancelling"
        return "running" if self.accepting_cancellation else "finalizing"


@dataclass
class _ActiveOperation:
    operation_id: int
    name: str
    started_at: float
    owner_thread_id: int
    token: CancellationToken


class OperationHandle:
    """Posesión exclusiva de una operación; también funciona como contexto."""

    def __init__(self, coordinator, operation_id, token):
        self._coordinator = coordinator
        self.operation_id = operation_id
        self.token = token
        self._release_lock = threading.Lock()
        self._released = False
        self._entered = False

    @property
    def state(self):
        state = self._coordinator.state
        if state is not None and state.operation_id == self.operation_id:
            return state
        return None

    @property
    def released(self):
        with self._release_lock:
            return self._released

    def cancel(self):
        with self._release_lock:
            if self._released:
                return False
            return self.token.cancel()

    def check_cancelled(self):
        self.token.raise_if_cancelled()

    def release(self):
        """Libera la exclusión. La operación es idempotente."""
        with self._release_lock:
            if self._released:
                return False
            self._released = True
        return self._coordinator._release(self.operation_id)

    def __enter__(self):
        with self._release_lock:
            if self._released:
                raise RuntimeError("La operación ya fue liberada.")
            if self._entered:
                raise RuntimeError("El handle de operación no es reentrante.")
            self._entered = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False


class OperationCoordinator:
    """Exclusión mutua y cancelación para operaciones largas de la aplicación."""

    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._condition = threading.Condition(threading.RLock())
        self._active = None
        self._next_operation_id = 1

    @property
    def state(self):
        """Devuelve una instantánea de la operación activa, o ``None``."""
        with self._condition:
            if self._active is None:
                return None
            active = self._active
            cancelled, accepting_cancellation = active.token.snapshot()
            return OperationState(
                operation_id=active.operation_id,
                name=active.name,
                started_at=active.started_at,
                owner_thread_id=active.owner_thread_id,
                cancel_requested=cancelled,
                accepting_cancellation=accepting_cancellation,
            )

    @property
    def is_busy(self):
        with self._condition:
            return self._active is not None

    def acquire(self, name, wait=False, timeout=None):
        """Adquiere la operación global o lanza :class:`OperationBusyError`.

        Con ``wait=True`` se puede esperar de forma acotada a que termine la
        operación anterior. ``timeout=None`` permite una espera indefinida.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("El nombre de la operación no puede estar vacío.")
        if timeout is not None and timeout < 0:
            raise ValueError("El timeout no puede ser negativo.")

        normalized_name = name.strip()
        deadline = None if timeout is None else self._clock() + timeout

        with self._condition:
            while self._active is not None:
                if not wait:
                    raise OperationBusyError(self.state)

                remaining = None
                if deadline is not None:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise OperationBusyError(self.state)
                self._condition.wait(remaining)

            operation_id = self._next_operation_id
            self._next_operation_id += 1
            token = CancellationToken()
            self._active = _ActiveOperation(
                operation_id=operation_id,
                name=normalized_name,
                started_at=self._clock(),
                owner_thread_id=threading.get_ident(),
                token=token,
            )
            return OperationHandle(self, operation_id, token)

    def try_acquire(self, name):
        """Adquiere sin esperar; devuelve ``None`` si ya hay una operación."""
        try:
            return self.acquire(name)
        except OperationBusyError:
            return None

    def operation(self, name, wait=False, timeout=None):
        """Atajo legible para ``with coordinator.operation(...):``."""
        return self.acquire(name, wait=wait, timeout=timeout)

    def cancel_current(self, expected_name=None, expected_operation_id=None):
        """Solicita cancelación a la operación activa.

        ``expected_operation_id`` evita carreras ABA incluso si dos operaciones
        consecutivas usan el mismo nombre. ``expected_name`` permite además una
        comprobación legible para la UI.
        """
        with self._condition:
            active = self._active
            if active is None:
                return False
            if expected_name is not None and active.name != expected_name:
                return False
            if (
                expected_operation_id is not None
                and active.operation_id != expected_operation_id
            ):
                return False
            changed = active.token.cancel()
            self._condition.notify_all()
            return changed

    def _release(self, operation_id):
        with self._condition:
            if (
                self._active is None
                or self._active.operation_id != operation_id
            ):
                return False
            self._active = None
            self._condition.notify_all()
            return True


GLOBAL_OPERATION_COORDINATOR = OperationCoordinator()

# Nombres en ambos idiomas para facilitar la segunda oleada de integración UI.
operation_coordinator = GLOBAL_OPERATION_COORDINATOR
coordinador_operaciones = GLOBAL_OPERATION_COORDINATOR
