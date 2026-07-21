import subprocess
import threading
import unittest
from unittest import mock

from core import processes
from core.operations import (
    CancellationToken,
    GLOBAL_OPERATION_COORDINATOR,
    OperationBusyError,
    OperationCancelled,
    OperationCoordinator,
    coordinador_operaciones,
    operation_coordinator,
)


class CommunicateProcess:
    def __init__(self):
        self.pid = 2468
        self.returncode = None
        self.calls = []

    def communicate(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise subprocess.TimeoutExpired(["tool"], kwargs["timeout"])
        self.returncode = 0
        return "salida", ""

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class ProcessRunnerTests(unittest.TestCase):
    def test_input_is_sent_only_on_first_communicate(self):
        process = CommunicateProcess()
        with mock.patch.object(processes.subprocess, "Popen", return_value=process):
            result = processes.run_process(
                ["tool"],
                input="secret\n",
                timeout=1,
                poll_interval=0.01,
            )

        self.assertEqual(result.stdout, "salida")
        self.assertEqual(process.calls[0]["input"], "secret\n")
        self.assertNotIn("input", process.calls[1])

    def test_cancelled_token_prevents_popen(self):
        token = CancellationToken()
        token.cancel()

        with mock.patch.object(processes.subprocess, "Popen") as popen:
            with self.assertRaises(OperationCancelled):
                processes.run_process(["tool"], cancel_token=token)

        popen.assert_not_called()

    def test_non_finite_timeout_is_rejected_before_popen(self):
        with mock.patch.object(processes.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "finito"):
                processes.run_process(["tool"], timeout=float("nan"))

        popen.assert_not_called()

    def test_completion_won_by_cancellation_preserves_result_without_kill(self):
        token = CancellationToken()

        class CompletedWhileCancelling(CommunicateProcess):
            def __init__(self):
                super().__init__()
                self.terminated = 0
                self.killed = 0

            def communicate(self, **kwargs):
                self.calls.append(kwargs)
                self.returncode = 0
                token.cancel()
                return "aplicado", ""

            def terminate(self):
                self.terminated += 1

            def kill(self):
                self.killed += 1

        process = CompletedWhileCancelling()
        with mock.patch.object(processes.subprocess, "Popen", return_value=process):
            with self.assertRaises(OperationCancelled) as caught:
                processes.run_process(["tool"], cancel_token=token)

        self.assertEqual(caught.exception.completed_process.stdout, "aplicado")
        self.assertEqual(process.terminated, 0)
        self.assertEqual(process.killed, 0)


class OperationCoordinatorTests(unittest.TestCase):
    def test_mutual_exclusion_is_thread_safe(self):
        coordinator = OperationCoordinator()
        owner = coordinator.acquire("benchmark")
        attempted = threading.Event()
        result = []

        def contend():
            try:
                coordinator.acquire("verificacion")
            except OperationBusyError as exc:
                result.append(exc.active_state.name)
            finally:
                attempted.set()

        thread = threading.Thread(target=contend)
        thread.start()
        self.assertTrue(attempted.wait(1))
        thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["benchmark"])
        self.assertEqual(coordinator.state.name, "benchmark")

        self.assertTrue(owner.release())
        with coordinator.operation("verificacion") as second:
            self.assertEqual(second.state.name, "verificacion")
        self.assertIsNone(coordinator.state)

    def test_cancellation_updates_state_and_token(self):
        coordinator = OperationCoordinator()

        with coordinator.operation("automatizacion") as operation:
            self.assertEqual(coordinator.state.status, "running")
            self.assertFalse(coordinator.cancel_current("benchmark"))
            self.assertTrue(coordinator.cancel_current("automatizacion"))
            self.assertEqual(coordinator.state.status, "cancelling")
            self.assertTrue(operation.token.cancelled)
            with self.assertRaises(OperationCancelled):
                operation.check_cancelled()

        self.assertIsNone(coordinator.state)
        self.assertFalse(coordinator.cancel_current())

    def test_cancel_current_accepts_matching_operation_id(self):
        coordinator = OperationCoordinator()

        with coordinator.operation("automatizacion") as operation:
            self.assertTrue(
                coordinator.cancel_current(
                    expected_operation_id=operation.operation_id
                )
            )
            self.assertTrue(operation.token.cancelled)

    def test_cancel_current_rejects_non_matching_operation_id(self):
        coordinator = OperationCoordinator()

        with coordinator.operation("automatizacion") as operation:
            self.assertFalse(
                coordinator.cancel_current(
                    expected_operation_id=operation.operation_id + 1
                )
            )
            self.assertFalse(operation.token.cancelled)

    def test_cancel_current_requires_matching_name_and_operation_id(self):
        coordinator = OperationCoordinator()

        with coordinator.operation("automatizacion") as operation:
            self.assertFalse(
                coordinator.cancel_current(
                    expected_name="benchmark",
                    expected_operation_id=operation.operation_id,
                )
            )
            self.assertFalse(
                coordinator.cancel_current(
                    expected_name="automatizacion",
                    expected_operation_id=operation.operation_id + 1,
                )
            )
            self.assertFalse(operation.token.cancelled)
            self.assertTrue(
                coordinator.cancel_current(
                    expected_name="automatizacion",
                    expected_operation_id=operation.operation_id,
                )
            )
            self.assertTrue(operation.token.cancelled)

    def test_concurrent_cancellation_has_one_winner(self):
        token = CancellationToken()
        barrier = threading.Barrier(8)
        results = []
        results_lock = threading.Lock()

        def cancel_at_once():
            barrier.wait()
            result = token.cancel()
            with results_lock:
                results.append(result)

        threads = [threading.Thread(target=cancel_at_once) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(1)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 7)

    def test_operation_handle_is_not_reentrant_or_reusable(self):
        coordinator = OperationCoordinator()
        operation = coordinator.operation("benchmark")

        with operation:
            with self.assertRaisesRegex(RuntimeError, "no es reentrante"):
                with operation:
                    pass

        with self.assertRaisesRegex(RuntimeError, "ya fue liberada"):
            with operation:
                pass

    def test_operation_id_prevents_same_name_aba_cancellation(self):
        coordinator = OperationCoordinator()
        first = coordinator.acquire("benchmark")
        first_id = first.operation_id
        first.release()
        second = coordinator.acquire("benchmark")

        self.assertFalse(
            coordinator.cancel_current(
                "benchmark", expected_operation_id=first_id
            )
        )
        self.assertFalse(second.token.cancelled)
        second.release()

    def test_handle_cancel_cannot_complete_after_release(self):
        coordinator = OperationCoordinator()
        operation = coordinator.acquire("benchmark")
        cancel_entered = threading.Event()
        allow_cancel = threading.Event()
        release_finished = threading.Event()
        results = []
        original_cancel = operation.token.cancel

        def slow_cancel():
            cancel_entered.set()
            allow_cancel.wait(1)
            return original_cancel()

        operation.token.cancel = slow_cancel

        cancel_thread = threading.Thread(
            target=lambda: results.append(("cancel", operation.cancel()))
        )
        release_thread = threading.Thread(
            target=lambda: (
                results.append(("release", operation.release())),
                release_finished.set(),
            )
        )
        cancel_thread.start()
        self.assertTrue(cancel_entered.wait(1))
        release_thread.start()
        self.assertFalse(release_finished.wait(0.05))
        allow_cancel.set()

        cancel_thread.join(1)
        release_thread.join(1)
        self.assertFalse(cancel_thread.is_alive())
        self.assertFalse(release_thread.is_alive())
        self.assertCountEqual(results, [("cancel", True), ("release", True)])
        self.assertIsNone(coordinator.state)

    def test_context_releases_after_exception(self):
        coordinator = OperationCoordinator()

        with self.assertRaisesRegex(RuntimeError, "failure"):
            with coordinator.operation("benchmark"):
                raise RuntimeError("failure")

        self.assertFalse(coordinator.is_busy)
        next_operation = coordinator.try_acquire("verificacion")
        self.assertIsNotNone(next_operation)
        next_operation.release()

    def test_waiting_operation_acquires_after_release(self):
        coordinator = OperationCoordinator()
        owner = coordinator.acquire("benchmark")
        waiting = threading.Event()
        acquired = threading.Event()

        def wait_for_turn():
            waiting.set()
            with coordinator.operation("verificacion", wait=True, timeout=1):
                acquired.set()

        thread = threading.Thread(target=wait_for_turn)
        thread.start()
        self.assertTrue(waiting.wait(1))
        owner.release()

        self.assertTrue(acquired.wait(1))
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(coordinator.state)

    def test_global_aliases_share_one_coordinator(self):
        self.assertIs(operation_coordinator, GLOBAL_OPERATION_COORDINATOR)
        self.assertIs(coordinador_operaciones, GLOBAL_OPERATION_COORDINATOR)


if __name__ == "__main__":
    unittest.main()
