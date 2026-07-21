import subprocess
import stat
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from core.operations import (
    CancellationToken,
    OperationCancelled,
    OperationCoordinator,
)
from core.scx import (
    BASE_SYSTEM_NAME,
    NOT_FOUND_RETURN_CODE,
    ScxCommandError,
    ScxManager,
    ScxRestorationError,
    ScxState,
    TIMEOUT_RETURN_CODE,
)


def trusted_path(name):
    return name if str(name).startswith("/") else f"/usr/bin/{name}"


def trust_executable(_path):
    return True


def metadata_validator(metadata_by_path):
    def validate(path):
        metadata = metadata_by_path[path]
        return ScxManager._validate_posix_executable_metadata(path, metadata)

    return validate


class FakePosixFilesystem:
    def __init__(self, entries, links=None):
        self.entries = entries
        self.links = links or {}

    def lstat(self, path):
        try:
            return self.entries[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    def readlink(self, path):
        try:
            return self.links[path]
        except KeyError as exc:
            raise OSError(f"{path} no es un enlace") from exc


def posix_metadata(kind, permissions, uid=0):
    return SimpleNamespace(st_mode=kind | permissions, st_uid=uid)


class RecordingRunner:
    def __init__(self, responder=None):
        self.calls = []
        self.responder = responder

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append((command, kwargs))
        if self.responder is not None:
            return self.responder(command, kwargs)
        return subprocess.CompletedProcess(command, 0, "ok", "")


class StatefulScxRunner:
    def __init__(self, scheduler=None, mode=None):
        self.state = (scheduler, mode) if scheduler is not None else (None, None)
        self.calls = []
        self.fail_start_for = set()

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append((command, kwargs))
        action = command[1] if len(command) > 1 else None

        if action == "get":
            scheduler, mode = self.state
            if scheduler is None:
                stdout = "STOPPED (Sistema Base)"
            else:
                stdout = f"RUNNING {scheduler} in {mode} mode"
            return subprocess.CompletedProcess(command, 0, stdout, "")

        if action == "list":
            return subprocess.CompletedProcess(command, 0, '["scx_a", "scx_b"]', "")

        if action == "stop":
            self.state = (None, None)
            return subprocess.CompletedProcess(command, 0, "stopped", "")

        if action in ("start", "switch"):
            scheduler = command[command.index("-s") + 1]
            mode = command[command.index("-m") + 1]
            if scheduler in self.fail_start_for:
                return subprocess.CompletedProcess(
                    command, 1, "", f"cannot start {scheduler}"
                )
            self.state = (scheduler, mode)
            return subprocess.CompletedProcess(command, 0, "started", "")

        return subprocess.CompletedProcess(command, 2, "", "unknown action")


class FakeOwnedProcess:
    def __init__(self, pid=4321, require_kill=False):
        self.pid = pid
        self.require_kill = require_kill
        self.alive = True
        self.terminated = 0
        self.killed = 0
        self.wait_timeouts = []

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated += 1
        if not self.require_kill:
            self.alive = False

    def kill(self):
        self.killed += 1
        self.alive = False

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.alive:
            raise subprocess.TimeoutExpired(["owned-process"], timeout)
        return 0


class PrivilegeBackendTests(unittest.TestCase):
    def test_root_owned_run0_symlink_chain_is_trusted(self):
        directory = stat.S_IFDIR
        regular = stat.S_IFREG
        symlink = stat.S_IFLNK
        fs = FakePosixFilesystem(
            {
                "/": posix_metadata(directory, 0o755),
                "/usr": posix_metadata(directory, 0o755),
                "/usr/bin": posix_metadata(directory, 0o755),
                "/usr/bin/run0": posix_metadata(symlink, 0o777),
                "/usr/bin/systemd-run": posix_metadata(regular, 0o755),
                "/usr/bin/timeout": posix_metadata(regular, 0o755),
                "/usr/bin/scxctl": posix_metadata(regular, 0o755),
            },
            {"/usr/bin/run0": "systemd-run"},
        )

        def validate(path):
            ScxManager._validate_posix_executable_path(
                path,
                lstat=fs.lstat,
                readlink=fs.readlink,
            )
            return True

        runner = RecordingRunner()
        manager = ScxManager(
            backend_privilegios="run0",
            subprocess_runner=runner,
            which=lambda name: f"/usr/bin/{name}",
            executable_validator=validate,
        )

        result = manager.ejecutar_con_sudo(["scxctl", "stop"])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(runner.calls[0][0][0:2], ["/usr/bin/run0", "--"])
        self.assertEqual(
            ScxManager._validate_posix_executable_path(
                "/usr/bin/run0",
                lstat=fs.lstat,
                readlink=fs.readlink,
            ),
            "/usr/bin/systemd-run",
        )

    def test_symlink_to_mutable_tree_or_target_is_rejected(self):
        directory = stat.S_IFDIR
        regular = stat.S_IFREG
        symlink = stat.S_IFLNK
        base_entries = {
            "/": posix_metadata(directory, 0o755),
            "/usr": posix_metadata(directory, 0o755),
            "/usr/bin": posix_metadata(directory, 0o755),
            "/opt": posix_metadata(directory, 0o755),
        }
        cases = (
            (
                "tmp-tree",
                {
                    **base_entries,
                    "/usr/bin/run0": posix_metadata(symlink, 0o777),
                    "/tmp": posix_metadata(directory, 0o1777),
                    "/tmp/run0": posix_metadata(regular, 0o755),
                },
                {"/usr/bin/run0": "/tmp/run0"},
                "directorio '/tmp' es escribible",
            ),
            (
                "mutable-target",
                {
                    **base_entries,
                    "/usr/bin/run0": posix_metadata(symlink, 0o777),
                    "/opt/run0": posix_metadata(regular, 0o775),
                },
                {"/usr/bin/run0": "/opt/run0"},
                "archivo es escribible",
            ),
            (
                "untrusted-link-owner",
                {
                    **base_entries,
                    "/usr/bin/run0": posix_metadata(symlink, 0o777, uid=1000),
                    "/usr/bin/systemd-run": posix_metadata(regular, 0o755),
                },
                {"/usr/bin/run0": "systemd-run"},
                "enlace simbólico '/usr/bin/run0' no pertenece a root",
            ),
        )

        for name, entries, links, expected in cases:
            with self.subTest(name=name):
                fs = FakePosixFilesystem(entries, links)
                with self.assertRaisesRegex(ValueError, expected):
                    ScxManager._validate_posix_executable_path(
                        "/usr/bin/run0",
                        lstat=fs.lstat,
                        readlink=fs.readlink,
                    )

    def test_parent_segments_are_resolved_after_symlinks(self):
        directory = stat.S_IFDIR
        regular = stat.S_IFREG
        symlink = stat.S_IFLNK
        fs = FakePosixFilesystem(
            {
                "/": posix_metadata(directory, 0o755),
                "/trusted": posix_metadata(directory, 0o755),
                "/trusted/link": posix_metadata(symlink, 0o777),
                # Una normalización lexical incorrecta aprobaría este archivo.
                "/trusted/tool": posix_metadata(regular, 0o755),
                "/tmp": posix_metadata(directory, 0o1777),
                "/tmp/tree": posix_metadata(directory, 0o755),
                "/tmp/tool": posix_metadata(regular, 0o755),
            },
            {"/trusted/link": "/tmp/tree"},
        )

        with self.assertRaisesRegex(ValueError, "directorio '/tmp' es escribible"):
            ScxManager._validate_posix_executable_path(
                "/trusted/link/../tool",
                lstat=fs.lstat,
                readlink=fs.readlink,
            )

    def test_sudo_backend_is_non_interactive_and_bounded(self):
        runner = RecordingRunner()
        manager = ScxManager(
            backend_privilegios="sudo",
            subprocess_runner=runner,
            which=lambda name: trusted_path(name)
            if name in ("sudo", "timeout", "scxctl")
            else None,
            executable_validator=trust_executable,
            command_timeout=7,
        )

        result = manager.ejecutar_con_sudo(["scxctl", "stop"])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            runner.calls[0][0],
            [
                "/usr/bin/sudo",
                "-n",
                "--",
                "/usr/bin/timeout",
                "--signal=TERM",
                "--kill-after=2s",
                "7s",
                "/usr/bin/scxctl",
                "stop",
            ],
        )
        self.assertEqual(runner.calls[0][1]["timeout"], 20)
        self.assertTrue(runner.calls[0][1]["capture_output"])
        self.assertTrue(runner.calls[0][1]["text"])

    def test_privileged_timeout_is_enforced_inside_root_context(self):
        runner = RecordingRunner(
            lambda command, _kwargs: subprocess.CompletedProcess(
                command,
                TIMEOUT_RETURN_CODE,
                "",
                "",
            )
        )
        validated = []

        def validate(path):
            validated.append(path)
            return True

        manager = ScxManager(
            backend_privilegios="sudo",
            subprocess_runner=runner,
            which=lambda name: trusted_path(name),
            executable_validator=validate,
            process_stop_timeout=0.5,
        )

        result = manager.ejecutar_con_sudo(
            ["scxctl", "stop"],
            timeout=3,
        )

        self.assertEqual(result.returncode, TIMEOUT_RETURN_CODE)
        self.assertEqual(
            runner.calls[0][0],
            [
                "/usr/bin/sudo",
                "-n",
                "--",
                "/usr/bin/timeout",
                "--signal=TERM",
                "--kill-after=0.5s",
                "3s",
                "/usr/bin/scxctl",
                "stop",
            ],
        )
        self.assertEqual(runner.calls[0][1]["timeout"], 14.5)
        self.assertIn("/usr/bin/timeout", validated)

    def test_sudo_availability_and_validation_have_auth_timeout(self):
        runner = RecordingRunner()
        manager = ScxManager(
            backend_privilegios="sudo",
            subprocess_runner=runner,
            which=lambda name: "/usr/bin/sudo" if name == "sudo" else None,
            executable_validator=trust_executable,
            auth_timeout=3,
        )

        self.assertTrue(manager.sudo_disponible())
        self.assertTrue(manager.validar_sudo("secret"))

        self.assertEqual(runner.calls[0][0], ["/usr/bin/sudo", "-n", "-v"])
        self.assertEqual(runner.calls[0][1]["timeout"], 3)
        self.assertEqual(runner.calls[1][0], ["/usr/bin/sudo", "-S", "-v"])
        self.assertEqual(runner.calls[1][1]["timeout"], 3)
        self.assertEqual(runner.calls[1][1]["input"], "secret\n")

    def test_run0_backend_does_not_consume_application_password(self):
        runner = RecordingRunner()
        manager = ScxManager(
            backend_privilegios="run0",
            subprocess_runner=runner,
            which=lambda name: trusted_path(name)
            if name in ("run0", "timeout", "scxctl")
            else None,
            executable_validator=trust_executable,
        )

        self.assertTrue(manager.sudo_disponible())
        self.assertTrue(manager.validar_sudo("must-not-be-piped"))
        self.assertEqual(runner.calls, [])

        result = manager.ejecutar_con_sudo(["scxctl", "start", "-s", "scx_a"])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            runner.calls[0][0],
            [
                "/usr/bin/run0",
                "--",
                "/usr/bin/timeout",
                "--signal=TERM",
                "--kill-after=2s",
                "15s",
                "/usr/bin/scxctl",
                "start",
                "-s",
                "scx_a",
            ],
        )

    def test_auto_detection_uses_valid_sudo_session(self):
        runner = RecordingRunner()
        manager = ScxManager(
            subprocess_runner=runner,
            which=lambda name: f"/usr/bin/{name}"
            if name in ("sudo", "run0", "timeout", "scxctl")
            else None,
            executable_validator=trust_executable,
        )
        manager._running_as_root = lambda: False

        self.assertTrue(manager.sudo_disponible())
        self.assertEqual(manager.backend_privilegiado, "sudo")
        manager.ejecutar_con_sudo(["scxctl", "stop"])

        self.assertEqual(runner.calls[0][0], ["/usr/bin/sudo", "-n", "-v"])
        self.assertEqual(
            runner.calls[1][0][:3], ["/usr/bin/sudo", "-n", "--"]
        )

    def test_resolved_scxctl_path_is_stable_after_path_changes(self):
        runner = RecordingRunner()
        resolutions = []

        def changing_path(name):
            resolutions.append(name)
            if name == "scxctl":
                return (
                    "/trusted/bin/scxctl"
                    if resolutions.count(name) == 1
                    else "/malicious/bin/scxctl"
                )
            return None

        manager = ScxManager(
            backend_privilegios="direct",
            subprocess_runner=runner,
            which=changing_path,
            executable_validator=trust_executable,
        )

        manager.scx_run(["scxctl", "get"])
        manager.ejecutar_con_sudo(["scxctl", "stop"])

        self.assertEqual(resolutions, ["scxctl"])
        self.assertEqual(
            [call[0][0] for call in runner.calls],
            ["/trusted/bin/scxctl", "/trusted/bin/scxctl"],
        )

    def test_relative_resolver_result_is_never_executed(self):
        runner = RecordingRunner()
        manager = ScxManager(
            backend_privilegios="direct",
            subprocess_runner=runner,
            which=lambda _name: "relative/bin/scxctl",
            executable_validator=trust_executable,
        )

        result = manager.ejecutar_con_sudo(["scxctl", "stop"])

        self.assertEqual(result.returncode, 126)
        self.assertIn("ruta no absoluta", result.stderr)
        self.assertEqual(runner.calls, [])

    def test_untrusted_sudo_metadata_blocks_password_and_execution(self):
        cases = (
            (
                "symlink",
                SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0),
                "enlace simbólico",
            ),
            (
                "owner",
                SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=1000),
                "no pertenece a root",
            ),
            (
                "permissions",
                SimpleNamespace(st_mode=stat.S_IFREG | 0o775, st_uid=0),
                "escribible por grupo u otros",
            ),
        )

        for name, metadata, expected_error in cases:
            with self.subTest(name=name):
                runner = RecordingRunner()
                manager = ScxManager(
                    backend_privilegios="sudo",
                    subprocess_runner=runner,
                    which=lambda binary: "/usr/bin/sudo"
                    if binary == "sudo"
                    else None,
                    executable_validator=metadata_validator(
                        {"/usr/bin/sudo": metadata}
                    ),
                )

                self.assertFalse(manager.validar_sudo("top-secret"))
                self.assertIn(expected_error, manager.ultimo_error)
                self.assertNotIn("top-secret", manager.ultimo_error)
                self.assertEqual(runner.calls, [])

    def test_untrusted_privileged_scxctl_is_rejected_before_sudo(self):
        runner = RecordingRunner()
        trusted = SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0)
        unsafe = SimpleNamespace(st_mode=stat.S_IFREG | 0o777, st_uid=0)
        manager = ScxManager(
            backend_privilegios="sudo",
            subprocess_runner=runner,
            which=lambda name: trusted_path(name)
            if name in ("sudo", "scxctl")
            else None,
            executable_validator=metadata_validator(
                {
                    "/usr/bin/sudo": trusted,
                    "/usr/bin/scxctl": unsafe,
                }
            ),
        )

        result = manager.ejecutar_con_sudo(["scxctl", "stop"])

        self.assertEqual(result.returncode, 126)
        self.assertIn("/usr/bin/scxctl", result.stderr)
        self.assertIn("escribible por grupo u otros", result.stderr)
        self.assertEqual(runner.calls, [])

    def test_absolute_secondary_executable_is_validated_but_data_is_not(self):
        runner = RecordingRunner()
        validated = []

        def validate(path):
            validated.append(path)
            return True

        manager = ScxManager(
            backend_privilegios="direct",
            subprocess_runner=runner,
            which=lambda name: "/usr/bin/timeout" if name == "timeout" else None,
            executable_validator=validate,
        )

        result = manager.ejecutar_con_sudo(
            ["timeout", "5", "/opt/scx_runner", "/tmp/input.data"]
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            runner.calls[0][0],
            [
                "/usr/bin/timeout",
                "5",
                "/opt/scx_runner",
                "/tmp/input.data",
            ],
        )
        self.assertEqual(validated, ["/usr/bin/timeout", "/opt/scx_runner"])

    def test_timeout_nested_name_is_resolved_once_without_path_fallback(self):
        runner = RecordingRunner()
        resolutions = []

        def changing_path(name):
            resolutions.append(name)
            occurrence = resolutions.count(name)
            return f"/{'trusted' if occurrence == 1 else 'malicious'}/bin/{name}"

        manager = ScxManager(
            backend_privilegios="direct",
            subprocess_runner=runner,
            which=changing_path,
            executable_validator=trust_executable,
        )

        manager.ejecutar_con_sudo(["timeout", "5", "scx_runner"])
        manager.ejecutar_con_sudo(["timeout", "5", "scx_runner"])

        self.assertEqual(resolutions, ["timeout", "scx_runner"])
        self.assertEqual(
            [call[0] for call in runner.calls],
            [
                ["/trusted/bin/timeout", "5", "/trusted/bin/scx_runner"],
                ["/trusted/bin/timeout", "5", "/trusted/bin/scx_runner"],
            ],
        )

    def test_timeout_end_of_options_skips_duration_before_command(self):
        runner = RecordingRunner()
        manager = ScxManager(
            backend_privilegios="direct",
            subprocess_runner=runner,
            which=lambda name: trusted_path(name),
            executable_validator=trust_executable,
        )

        result = manager.ejecutar_con_sudo(
            ["timeout", "--", "5", "scx_runner"]
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            runner.calls[0][0],
            ["/usr/bin/timeout", "--", "5", "/usr/bin/scx_runner"],
        )

    def test_sudo_password_is_redacted_from_errors(self):
        password = "never-log-this"

        def echo_password(command, kwargs):
            return subprocess.CompletedProcess(
                command,
                1,
                f"stdout {kwargs['input']}",
                f"authentication failed for {kwargs['input']}",
            )

        runner = RecordingRunner(echo_password)
        manager = ScxManager(
            backend_privilegios="sudo",
            subprocess_runner=runner,
            which=lambda name: "/usr/bin/sudo" if name == "sudo" else None,
            executable_validator=trust_executable,
        )

        self.assertFalse(manager.validar_sudo(password))

        self.assertEqual(runner.calls[0][0], ["/usr/bin/sudo", "-S", "-v"])
        self.assertNotIn(password, " ".join(runner.calls[0][0]))
        self.assertNotIn(password, manager.ultimo_error)
        self.assertIn("[REDACTADO]", manager.ultimo_error)

    def test_sudo_failure_preserves_useful_stdout(self):
        runner = RecordingRunner(
            lambda command, _kwargs: subprocess.CompletedProcess(
                command, 1, "policy denied", ""
            )
        )
        manager = ScxManager(
            backend_privilegios="sudo",
            subprocess_runner=runner,
            which=lambda name: trusted_path(name),
            executable_validator=trust_executable,
        )

        manager.ejecutar_con_sudo(["scxctl", "stop"])

        self.assertEqual(manager.ultimo_error, "policy denied")

    def test_missing_configured_backend_is_clear(self):
        runner = RecordingRunner()
        manager = ScxManager(
            backend_privilegios="run0",
            subprocess_runner=runner,
            which=lambda _name: None,
        )

        result = manager.ejecutar_con_sudo(["scxctl", "stop"])

        self.assertEqual(result.returncode, NOT_FOUND_RETURN_CODE)
        self.assertIn("run0", result.stderr)
        self.assertEqual(runner.calls, [])


class CommandSafetyTests(unittest.TestCase):
    def test_default_runner_uses_cancelable_popen_path(self):
        token = CancellationToken()
        completed = subprocess.CompletedProcess(
            ["/usr/bin/scxctl", "get"],
            0,
            "STOPPED (Sistema Base)",
            "",
        )
        with mock.patch("core.scx.run_process", return_value=completed) as run:
            manager = ScxManager(which=trusted_path)
            state = manager.obtener_estado(cancel_token=token)

        self.assertEqual(state, (None, None))
        run.assert_called_once()
        self.assertIs(run.call_args.kwargs["cancel_token"], token)
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.PIPE)

    def test_cancelled_commands_never_reach_the_runner(self):
        token = CancellationToken()
        token.cancel()

        for method_name in ("scx_run", "ejecutar_con_sudo"):
            with self.subTest(method=method_name):
                runner = RecordingRunner()
                manager = ScxManager(
                    backend_privilegios="direct",
                    subprocess_runner=runner,
                    which=trusted_path,
                    executable_validator=trust_executable,
                )
                method = getattr(manager, method_name)

                with self.assertRaises(OperationCancelled):
                    method(["scxctl", "get"], cancel_token=token)

                self.assertEqual(runner.calls, [])

    def test_operation_cancelled_from_runner_is_not_captured(self):
        def cancel(_command, _kwargs):
            raise OperationCancelled("cancelado durante Popen")

        manager = ScxManager(
            subprocess_runner=RecordingRunner(cancel),
            which=trusted_path,
        )

        with self.assertRaisesRegex(OperationCancelled, "durante Popen"):
            manager.scx_run(
                ["scxctl", "get"],
                cancel_token=CancellationToken(),
            )

    def test_timeout_becomes_completed_process(self):
        def time_out(command, kwargs):
            raise subprocess.TimeoutExpired(
                command,
                kwargs["timeout"],
                output=b"partial",
                stderr=b"late output",
            )

        runner = RecordingRunner(time_out)
        manager = ScxManager(
            subprocess_runner=runner,
            which=trusted_path,
            command_timeout=0.25,
        )

        result = manager.scx_run(["scxctl", "get"])

        self.assertEqual(result.returncode, TIMEOUT_RETURN_CODE)
        self.assertEqual(result.stdout, "partial")
        self.assertIn("Tiempo de espera agotado", result.stderr)
        self.assertEqual(runner.calls[0][1]["timeout"], 0.25)

    def test_non_finite_timeout_never_reaches_runner(self):
        runner = RecordingRunner()
        manager = ScxManager(
            backend_privilegios="direct",
            subprocess_runner=runner,
            which=trusted_path,
            executable_validator=trust_executable,
        )

        result = manager.ejecutar_con_sudo(
            ["scxctl", "stop"],
            timeout=float("nan"),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("finito", result.stderr)
        self.assertEqual(runner.calls, [])

    def test_missing_binary_becomes_completed_process(self):
        def missing(_command, _kwargs):
            raise FileNotFoundError("missing")

        manager = ScxManager(
            subprocess_runner=RecordingRunner(missing),
            which=lambda _name: None,
        )

        result = manager.scx_run(["scxctl", "get"])

        self.assertEqual(result.returncode, NOT_FOUND_RETURN_CODE)
        self.assertIn("scxctl", result.stderr)

    def test_failed_get_and_list_stdout_are_not_parsed(self):
        def failed_output(command, _kwargs):
            action = command[1]
            stdout = (
                "RUNNING malicious in gaming mode"
                if action == "get"
                else '["malicious"]'
            )
            return subprocess.CompletedProcess(command, 9, stdout, "command failed")

        manager = ScxManager(
            subprocess_runner=RecordingRunner(failed_output),
            which=trusted_path,
        )

        with self.assertRaises(ScxCommandError):
            manager.obtener_estado()
        self.assertEqual(manager.obtener_lista(), [])

    def test_failed_list_preserves_the_best_available_diagnostic(self):
        cases = (
            ("stdout detail", "stderr detail", "stderr detail"),
            ("stdout detail", "", "stdout detail"),
            ("", "", "código de salida 9"),
        )

        for stdout, stderr, expected in cases:
            with self.subTest(expected=expected):
                runner = RecordingRunner(
                    lambda command, _kwargs: subprocess.CompletedProcess(
                        command, 9, stdout, stderr
                    )
                )
                manager = ScxManager(subprocess_runner=runner, which=trusted_path)

                self.assertEqual(manager.obtener_lista(), [])
                self.assertEqual(manager.ultimo_error, expected)

    def test_public_state_parser_is_strict(self):
        cases = (
            ("NOT RUNNING", ScxState()),
            ("RUNNING scx_lavd (gaming)", ScxState("scx_lavd", "gaming")),
            ("stopped", ScxState()),
        )

        for output, expected in cases:
            with self.subTest(output=output):
                self.assertEqual(ScxManager.parsear_estado(output), expected)

        with self.assertRaises(ValueError):
            ScxManager.parsear_estado("unexpected output")

    def test_real_scxctl_state_format_is_canonical(self):
        cases = (
            (
                'running Flash with arguments "--slice-us 20000" '
                "in LowLatency mode",
                ScxState("flash", "lowlatency"),
            ),
            (
                'running Flash with arguments "--slice-us 20000"',
                ScxState("flash", "auto"),
            ),
            (
                "running P2DQ in PowerSave mode",
                ScxState("p2dq", "powersave"),
            ),
        )

        for output, expected in cases:
            with self.subTest(output=output):
                self.assertEqual(ScxManager.parsear_estado(output), expected)

        self.assertEqual(
            ScxState("Flash", "Auto"),
            ScxState("flash", "auto"),
        )
        self.assertEqual(
            ScxState("FLASH", "Power-Save"),
            ScxState("flash", "powersave"),
        )

    def test_list_and_cli_arguments_use_canonical_names(self):
        def responder(command, _kwargs):
            if command[1] == "list":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    'supported schedulers: ["Flash", "P2DQ", "flash"]',
                    "",
                )
            return subprocess.CompletedProcess(command, 0, "started", "")

        runner = RecordingRunner(responder)
        manager = ScxManager(
            backend_privilegios="direct",
            subprocess_runner=runner,
            which=trusted_path,
            executable_validator=trust_executable,
        )

        self.assertEqual(manager.obtener_lista(), ["flash", "p2dq"])
        self.assertEqual(manager.obtener_lista(["FLASH"]), ["flash"])
        manager.ejecutar_con_sudo(
            ["scxctl", "start", "--sched=Flash", "--mode=LowLatency"]
        )

        self.assertEqual(
            runner.calls[-1][0],
            [
                "/usr/bin/scxctl",
                "start",
                "--sched=flash",
                "--mode=lowlatency",
            ],
        )

    def test_not_running_output_is_never_parsed_as_active(self):
        runner = RecordingRunner(
            lambda command, _kwargs: subprocess.CompletedProcess(
                command, 0, "NOT RUNNING scx_foreign in gaming mode", ""
            )
        )
        manager = ScxManager(subprocess_runner=runner, which=trusted_path)

        self.assertEqual(manager.obtener_estado(), (None, None))

    def test_running_line_wins_over_unrelated_stopped_history(self):
        output = (
            "RUNNING scx_lavd in auto mode\n"
            "previous scheduler stopped cleanly"
        )
        runner = RecordingRunner(
            lambda command, _kwargs: subprocess.CompletedProcess(
                command, 0, output, ""
            )
        )
        manager = ScxManager(subprocess_runner=runner, which=trusted_path)

        self.assertEqual(manager.obtener_estado(), ("scx_lavd", "auto"))

    def test_strict_state_capture_rejects_unknown_success_output(self):
        runner = RecordingRunner(
            lambda command, _kwargs: subprocess.CompletedProcess(
                command, 0, "unexpected output", ""
            )
        )
        manager = ScxManager(subprocess_runner=runner, which=trusted_path)

        with self.assertRaises(ScxCommandError):
            manager.capturar_estado()

        self.assertIn("no reconocido", manager.ultimo_error)

    def test_detener_todos_never_invokes_pattern_killing(self):
        runner = RecordingRunner()
        manager = ScxManager(
            backend_privilegios="direct",
            subprocess_runner=runner,
            which=trusted_path,
            executable_validator=trust_executable,
        )

        result = manager.detener_todos()

        self.assertEqual(result.returncode, 0)
        commands = [call[0] for call in runner.calls]
        self.assertEqual(commands, [["/usr/bin/scxctl", "stop"]])
        self.assertFalse(any("pkill" in command for command in commands))

    def test_only_registered_process_handles_are_terminated(self):
        runner = RecordingRunner()
        manager = ScxManager(
            backend_privilegios="direct",
            subprocess_runner=runner,
            which=trusted_path,
            executable_validator=trust_executable,
            process_stop_timeout=0.5,
        )
        process = manager.registrar_proceso_propietario(
            FakeOwnedProcess(require_kill=True)
        )

        result = manager.detener_todos()

        self.assertEqual(result.returncode, 0)
        self.assertEqual(process.terminated, 1)
        self.assertEqual(process.killed, 1)
        self.assertEqual(process.wait_timeouts, [0.5, 0.5])
        self.assertEqual(manager.pids_propios, ())

    def test_developer_mode_is_deterministic_and_starts_stopped(self):
        manager = ScxManager(modo_desarrollador=True)

        self.assertEqual(manager.obtener_estado(), (None, None))
        self.assertIn(BASE_SYSTEM_NAME, manager.scx_run(["scxctl", "get"]).stdout)
        self.assertEqual(manager.obtener_lista(), list(manager.SIMULATED_SCHEDULERS))

        manager.ejecutar_con_sudo(
            ["scxctl", "start", "-s", "scx_lavd", "-m", "gaming"]
        )
        self.assertEqual(manager.obtener_estado(), ("scx_lavd", "gaming"))
        manager.detener_todos()
        self.assertEqual(manager.obtener_estado(), (None, None))

        manager.ejecutar_con_sudo(["scxctl", "start", "-s", "scx_rusty"])
        manager.modo_desarrollador = False
        manager.modo_desarrollador = True
        self.assertEqual(manager.obtener_estado(), (None, None))

    def test_developer_default_start_is_tracked_by_a_session(self):
        manager = ScxManager(modo_desarrollador=True)

        with manager.sesion():
            result = manager.ejecutar_con_sudo(["scxctl", "start"])
            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                manager.obtener_estado(),
                (manager.SIMULATED_SCHEDULERS[0], "auto"),
            )

        self.assertEqual(manager.obtener_estado(), (None, None))

    def test_developer_mode_setter_does_not_wait_for_state_subprocess(self):
        command_started = threading.Event()
        release_command = threading.Event()
        setter_finished = threading.Event()

        def block_get(command, _kwargs):
            command_started.set()
            release_command.wait(1)
            return subprocess.CompletedProcess(
                command,
                0,
                "STOPPED (Sistema Base)",
                "",
            )

        manager = ScxManager(
            subprocess_runner=RecordingRunner(block_get),
            which=trusted_path,
        )
        command_thread = threading.Thread(
            target=lambda: manager.scx_run(["scxctl", "get"])
        )
        setter_thread = threading.Thread(
            target=lambda: (
                setattr(manager, "modo_desarrollador", True),
                setter_finished.set(),
            )
        )

        command_thread.start()
        self.assertTrue(command_started.wait(1))
        setter_thread.start()
        setter_was_non_blocking = setter_finished.wait(0.2)
        release_command.set()
        command_thread.join(1)
        setter_thread.join(1)

        self.assertTrue(setter_was_non_blocking)
        self.assertFalse(command_thread.is_alive())
        self.assertFalse(setter_thread.is_alive())
        self.assertTrue(manager.modo_desarrollador)

    def test_mode_change_cannot_split_real_stop_and_start(self):
        stop_started = threading.Event()
        release_stop = threading.Event()

        class BlockingStopRunner(StatefulScxRunner):
            def __call__(self, command, **kwargs):
                if len(command) > 1 and command[1] == "stop":
                    stop_started.set()
                    release_stop.wait(1)
                return super().__call__(command, **kwargs)

        runner = BlockingStopRunner("scx_initial", "auto")
        manager = ScxManager(
            backend_privilegios="direct",
            subprocess_runner=runner,
            which=trusted_path,
            executable_validator=trust_executable,
        )
        errors = []
        transition = threading.Thread(
            target=lambda: self._capture_error(
                errors,
                lambda: manager.restaurar_estado(ScxState("flash", "auto")),
            )
        )

        transition.start()
        self.assertTrue(stop_started.wait(1))
        manager.modo_desarrollador = True
        release_stop.set()
        transition.join(1)

        self.assertFalse(transition.is_alive())
        self.assertEqual(runner.state, ("flash", "auto"))
        self.assertEqual(
            [call[0][1] for call in runner.calls],
            ["stop", "start"],
        )
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ScxRestorationError)
        self.assertIn("configuración SCX cambió", str(errors[0]))

    @staticmethod
    def _capture_error(errors, callback):
        try:
            callback()
        except BaseException as exc:
            errors.append(exc)


class ScxSessionTests(unittest.TestCase):
    @staticmethod
    def manager_for(runner):
        return ScxManager(
            backend_privilegios="direct",
            subprocess_runner=runner,
            which=trusted_path,
            executable_validator=trust_executable,
        )

    def test_session_restores_initial_state_after_original_error(self):
        runner = StatefulScxRunner("scx_initial", "powersave")
        manager = self.manager_for(runner)
        original_error = RuntimeError("benchmark failed")

        with self.assertRaises(RuntimeError) as caught:
            with manager.sesion() as session:
                manager.ejecutar_con_sudo(
                    ["scxctl", "switch", "-s", "scx_candidate", "-m", "auto"]
                )
                session.conservar_ganador("scx_candidate")
                raise original_error

        self.assertIs(caught.exception, original_error)
        self.assertEqual(runner.state, ("scx_initial", "powersave"))
        self.assertIsNone(session.restore_error)

    def test_cancelled_session_does_not_capture_initial_state(self):
        token = CancellationToken()
        token.cancel()
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with self.assertRaises(OperationCancelled):
            with manager.sesion(token):
                pass

        self.assertEqual(runner.calls, [])

    def test_session_apply_updates_expected_state_and_revision(self):
        runner = StatefulScxRunner("scx_initial", "PowerSave")
        manager = self.manager_for(runner)

        with manager.sesion() as session:
            first_revision = session._expected_revision
            session.aplicar(ScxState("Flash", "Auto"))
            self.assertEqual(session._expected_state, ScxState("flash", "auto"))
            self.assertGreater(session._expected_revision, first_revision)

            revision_after_first = session._expected_revision
            session.aplicar(ScxState("P2DQ", "LowLatency"))
            self.assertEqual(
                session._expected_state,
                ScxState("p2dq", "lowlatency"),
            )
            self.assertGreater(session._expected_revision, revision_after_first)

        self.assertEqual(runner.state, ("scx_initial", "powersave"))

    def test_failed_second_apply_restores_initial_state(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with self.assertRaises(ScxRestorationError) as caught:
            with manager.sesion() as session:
                session.aplicar(ScxState("flash", "auto"))
                runner.fail_start_for.add("p2dq")
                session.aplicar(ScxState("p2dq", "lowlatency"))

        self.assertIn("cannot start p2dq", str(caught.exception))
        self.assertEqual(runner.state, ("scx_initial", "auto"))

    def test_cancellation_after_apply_stop_still_restores_initial_state(self):
        coordinator = OperationCoordinator()

        class CancellingStopRunner(StatefulScxRunner):
            cancel_on_stop = False

            def __call__(self, command, **kwargs):
                result = super().__call__(command, **kwargs)
                if self.cancel_on_stop and command[1] == "stop":
                    self.cancel_on_stop = False
                    coordinator.cancel_current("automatizacion")
                return result

        runner = CancellingStopRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with self.assertRaises(OperationCancelled):
            with coordinator.operation("automatizacion") as operation:
                with manager.sesion(operation.token) as session:
                    session.aplicar(ScxState("flash", "auto"))
                    runner.cancel_on_stop = True
                    session.aplicar(ScxState("p2dq", "lowlatency"))

        self.assertEqual(runner.state, ("scx_initial", "auto"))

    def test_cancellation_after_completed_start_still_restores_initial_state(self):
        coordinator = OperationCoordinator()

        class CancellingStartRunner(StatefulScxRunner):
            def __call__(self, command, **kwargs):
                result = super().__call__(command, **kwargs)
                if command[1] == "start" and command[command.index("-s") + 1] == "flash":
                    coordinator.cancel_current("automatizacion")
                return result

        runner = CancellingStartRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with self.assertRaises(OperationCancelled):
            with coordinator.operation("automatizacion") as operation:
                with manager.sesion(operation.token) as session:
                    session.aplicar(ScxState("flash", "auto"))

        self.assertEqual(runner.state, ("scx_initial", "auto"))

    def test_external_change_between_session_applies_never_stops_it(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with manager.sesion() as session:
            session.aplicar(ScxState("Flash", "Auto"))
            runner.state = ("scx_admin", "PowerSave")
            calls_before_second_apply = len(runner.calls)

            with self.assertRaises(ScxRestorationError) as caught:
                session.aplicar(ScxState("P2DQ", "LowLatency"))

            actions = [
                call[0][1] for call in runner.calls[calls_before_second_apply:]
            ]
            self.assertEqual(actions, ["get"])
            self.assertEqual(runner.state, ("scx_admin", "PowerSave"))
            self.assertIn("scx_admin (powersave)", str(caught.exception))

            # Restablece solo el doble de prueba para permitir comprobar también
            # la restauración normal al abandonar el contexto.
            runner.state = ("FLASH", "AUTO")

        self.assertEqual(runner.state, ("scx_initial", "auto"))

    def test_other_thread_manager_change_is_not_claimed_by_session(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with self.assertRaises(ScxRestorationError):
            with manager.sesion():
                thread = threading.Thread(
                    target=lambda: manager.ejecutar_con_sudo(
                        ["scxctl", "switch", "-s", "scx_admin", "-m", "gaming"]
                    )
                )
                thread.start()
                thread.join(1)
                self.assertFalse(thread.is_alive())

        self.assertEqual(runner.state, ("scx_admin", "gaming"))

    def test_session_apply_is_rejected_after_context_exit(self):
        manager = self.manager_for(StatefulScxRunner())

        with manager.sesion() as session:
            pass

        with self.assertRaisesRegex(RuntimeError, "debe estar activa"):
            session.aplicar(ScxState("flash", "auto"))

    def test_session_restores_after_cooperative_cancellation(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)
        coordinator = OperationCoordinator()

        with self.assertRaises(OperationCancelled):
            with coordinator.operation("automatizacion") as operation:
                with manager.sesion(operation.token) as session:
                    manager.ejecutar_con_sudo(
                        ["scxctl", "switch", "-s", "scx_candidate", "-m", "gaming"]
                    )
                    coordinator.cancel_current("automatizacion")
                    session.comprobar_cancelacion()

        self.assertEqual(runner.state, ("scx_initial", "auto"))
        self.assertIsNone(coordinator.state)

    def test_winner_is_kept_only_when_explicit_and_successful(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with manager.sesion() as session:
            manager.ejecutar_con_sudo(
                ["scxctl", "switch", "-s", "scx_candidate", "-m", "auto"]
            )
            session.conservar_ganador("scx_winner", "gaming")

        self.assertEqual(runner.state, ("scx_winner", "gaming"))

    def test_success_without_winner_restores_initial_state(self):
        runner = StatefulScxRunner("scx_initial", "powersave")
        manager = self.manager_for(runner)

        with manager.sesion():
            manager.ejecutar_con_sudo(
                ["scxctl", "switch", "-s", "scx_candidate", "-m", "gaming"]
            )

        self.assertEqual(runner.state, ("scx_initial", "powersave"))

    def test_latest_manager_state_is_expected_before_restoration(self):
        runner = StatefulScxRunner("scx_initial", "powersave")
        manager = self.manager_for(runner)

        with manager.sesion():
            manager.ejecutar_con_sudo(
                ["scxctl", "switch", "-s", "scx_candidate", "-m", "gaming"]
            )
            calls_before_exit = len(runner.calls)

        actions = [call[0][1] for call in runner.calls[calls_before_exit:]]
        self.assertEqual(actions, ["get", "stop", "start"])
        self.assertEqual(runner.state, ("scx_initial", "powersave"))

    def test_external_state_change_is_not_overwritten(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with self.assertRaises(ScxRestorationError) as caught:
            with manager.sesion() as session:
                manager.ejecutar_con_sudo(
                    ["scxctl", "switch", "-s", "scx_candidate", "-m", "auto"]
                )
                calls_before_exit = len(runner.calls)
                runner.state = ("scx_admin", "gaming")

        actions = [call[0][1] for call in runner.calls[calls_before_exit:]]
        self.assertEqual(actions, ["get"])
        self.assertEqual(runner.state, ("scx_admin", "gaming"))
        self.assertIs(caught.exception, session.restore_error)
        self.assertIn("cambió fuera de esta sesión", str(caught.exception))
        self.assertIn("scx_candidate (auto)", str(caught.exception))
        self.assertIn("scx_admin (gaming)", str(caught.exception))

    def test_external_scheduler_crash_to_base_is_not_restarted(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with self.assertRaises(ScxRestorationError) as caught:
            with manager.sesion():
                manager.ejecutar_con_sudo(
                    ["scxctl", "switch", "-s", "scx_candidate", "-m", "gaming"]
                )
                calls_before_exit = len(runner.calls)
                runner.state = (None, None)

        actions = [call[0][1] for call in runner.calls[calls_before_exit:]]
        self.assertEqual(actions, ["get"])
        self.assertEqual(runner.state, (None, None))
        self.assertIn("Sistema Base", str(caught.exception))

    def test_mode_change_invalidates_an_open_session(self):
        runner = StatefulScxRunner()
        manager = self.manager_for(runner)

        with self.assertRaises(ScxRestorationError) as caught:
            with manager.sesion() as session:
                calls_before_exit = len(runner.calls)
                manager.modo_desarrollador = True

        self.assertEqual(len(runner.calls), calls_before_exit)
        self.assertEqual(runner.state, (None, None))
        self.assertIs(caught.exception, session.restore_error)
        self.assertIn("no sobrescribir", str(caught.exception))

    def test_external_change_preserves_original_error_and_foreign_state(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)
        original_error = RuntimeError("benchmark crashed")

        with self.assertRaises(ScxRestorationError) as caught:
            with manager.sesion() as session:
                calls_before_exit = len(runner.calls)
                runner.state = ("scx_admin", "gaming")
                raise original_error

        actions = [call[0][1] for call in runner.calls[calls_before_exit:]]
        self.assertEqual(actions, ["get"])
        self.assertEqual(runner.state, ("scx_admin", "gaming"))
        self.assertIs(caught.exception.__cause__, original_error)
        self.assertIsInstance(session.restore_error, ScxRestorationError)
        self.assertIn("RuntimeError", str(caught.exception))
        self.assertIn("benchmark crashed", str(caught.exception))

    def test_restore_failure_reports_original_exception(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)
        original_error = ValueError("measurement failed")

        with self.assertRaises(ScxRestorationError) as caught:
            with manager.sesion() as session:
                runner.fail_start_for.add("scx_initial")
                raise original_error

        self.assertIsInstance(caught.exception, ScxRestorationError)
        self.assertIn("cannot start scx_initial", str(caught.exception))
        self.assertIn("ValueError", str(caught.exception))
        self.assertIn("measurement failed", str(caught.exception))
        self.assertIs(caught.exception.__cause__, original_error)
        self.assertIsInstance(session.restore_error, ScxRestorationError)

    def test_restore_failure_reports_original_cancellation(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)
        coordinator = OperationCoordinator()

        with self.assertRaises(ScxRestorationError) as caught:
            with coordinator.operation("automatizacion") as operation:
                with manager.sesion(operation.token) as session:
                    runner.fail_start_for.add("scx_initial")
                    coordinator.cancel_current(
                        "automatizacion",
                        expected_operation_id=operation.operation_id,
                    )
                    session.comprobar_cancelacion()

        self.assertIsInstance(caught.exception, ScxRestorationError)
        self.assertIn("cannot start scx_initial", str(caught.exception))
        self.assertIn("OperationCancelled", str(caught.exception))
        self.assertIn("La operación fue cancelada", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, OperationCancelled)
        self.assertIsInstance(session.restore_error, ScxRestorationError)
        self.assertIsNone(coordinator.state)

    def test_restore_failure_without_original_error_is_raised_unchanged(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with self.assertRaises(ScxRestorationError) as caught:
            with manager.sesion() as session:
                runner.fail_start_for.add("scx_initial")

        self.assertIs(caught.exception, session.restore_error)
        self.assertIn("cannot start scx_initial", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_timeout_with_not_running_text_is_not_a_benign_stop(self):
        def timed_out_stop(command, _kwargs):
            if command[1] == "stop":
                return subprocess.CompletedProcess(
                    command,
                    TIMEOUT_RETURN_CODE,
                    "not running",
                    "Tiempo de espera agotado",
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        manager = self.manager_for(RecordingRunner(timed_out_stop))

        with self.assertRaises(ScxRestorationError):
            manager.restaurar_estado(ScxState())

    def test_unrelated_stop_error_is_not_treated_as_benign(self):
        def failed_stop(command, _kwargs):
            if command[1] == "stop":
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "not running",
                    "D-Bus connection failed",
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        manager = self.manager_for(RecordingRunner(failed_stop))

        with self.assertRaises(ScxRestorationError):
            manager.restaurar_estado(ScxState())

    def test_token_without_atomic_seal_cannot_keep_winner(self):
        class LegacyToken:
            cancelled = False

            def raise_if_cancelled(self):
                return None

        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with manager.sesion(LegacyToken()) as session:
            session.conservar_ganador("scx_winner", "gaming")

        self.assertEqual(runner.state, ("scx_initial", "auto"))

    def test_duck_typed_cancelled_method_is_evaluated(self):
        class MethodToken:
            def cancelled(self):
                return False

        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)

        with manager.sesion(MethodToken()):
            manager.ejecutar_con_sudo(
                ["scxctl", "switch", "-s", "scx_candidate", "-m", "gaming"]
            )

        self.assertEqual(runner.state, ("scx_initial", "auto"))

    def test_winner_finalization_closes_cancellation_race(self):
        runner = StatefulScxRunner("scx_initial", "auto")
        manager = self.manager_for(runner)
        coordinator = OperationCoordinator()
        cancellation_results = []
        original_restore = manager.restaurar_estado

        with coordinator.operation("automatizacion") as operation:
            original_restore_method = manager.restaurar_estado

            def restore_while_cancelling(state):
                cancellation_results.append(
                    coordinator.cancel_current(
                        "automatizacion",
                        expected_operation_id=operation.operation_id,
                    )
                )
                return original_restore(state)

            manager.restaurar_estado = restore_while_cancelling
            try:
                with manager.sesion(operation.token) as session:
                    session.conservar_ganador("scx_winner", "gaming")
            finally:
                manager.restaurar_estado = original_restore_method

        self.assertEqual(cancellation_results, [False])
        self.assertEqual(runner.state, ("scx_winner", "gaming"))


if __name__ == "__main__":
    unittest.main()
