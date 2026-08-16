from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .paths import engine_path


CREATE_NO_WINDOW = 0x08000000


def _descendant_process_ids(process_id: int) -> list[int]:
    """Return the process ids spawned by ``process_id``, deepest last."""
    if process_id <= 0:
        return []
    query = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId | ConvertTo-Csv -NoTypeInformation"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in completed.stdout.splitlines()[1:]:
        parts = line.replace('"', "").split(",")
        if len(parts) != 2:
            continue
        try:
            child, parent = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(parent, []).append(child)

    found: list[int] = []
    pending = [process_id]
    while pending:
        current = pending.pop()
        for child in children.get(current, []):
            if child not in found and child != process_id:
                found.append(child)
                pending.append(child)
    return found


def _force_kill(process_id: int, tree: bool = False) -> None:
    if process_id <= 0:
        return
    command = ["taskkill.exe", "/PID", str(process_id), "/F"]
    if tree:
        command.insert(-1, "/T")
    try:
        subprocess.run(
            command, capture_output=True, timeout=5, creationflags=CREATE_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError):
        pass


class EngineManager(QObject):
    status_changed = Signal(str)
    log_received = Signal(str)

    def __init__(self, data_root: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.data_root = Path(data_root)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.started.connect(lambda: self._set_status("running"))
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_error)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self._status = "stopped"

    @property
    def status(self) -> str:
        return self._status

    @property
    def is_running(self) -> bool:
        return self.process.state() != QProcess.NotRunning

    def start(self) -> None:
        if self.is_running:
            return
        target = engine_path()
        if not target.exists():
            self.log_received.emit("Mind engine file is missing.")
            self._set_status("error")
            return

        environment = QProcessEnvironment.systemEnvironment()
        environment.remove("_MEIPASS2")
        environment.remove("_MEIPASS")
        environment.remove("PYTHONHOME")
        environment.remove("PYTHONPATH")
        environment.insert("MIND_DATA_DIR", str(self.data_root))
        environment.insert("MIND_ENGINE_EMBEDDED", "1")
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(self.data_root))
        self.process.setProgram(sys.executable)
        if getattr(sys, "frozen", False):
            self.process.setArguments(["--engine", "--debug"])
        else:
            self.process.setArguments(["-u", str(target), "--debug"])
        self._set_status("starting")
        self.process.start()

    def stop(self) -> None:
        if not self.is_running:
            self._set_status("stopped")
            return
        self._set_status("stopping")
        self.process.terminate()
        QTimer.singleShot(2500, self._kill_if_needed)

    def restart(self) -> None:
        if not self.is_running:
            self.start()
            return
        self.process.finished.connect(self._restart_after_finish)
        self.stop()

    def shutdown(self) -> None:
        """Stop the engine and every process it spawned.

        The engine runs as a child interpreter, and in the packaged build as a
        second one-file bootloader with its own Python child. Terminating only
        the direct child leaves that grandchild alive holding a lock on
        Mind.exe, which blocks the updater from replacing the executable and
        forces the user to end the task by hand.

        Descendants are recorded before the parent is signalled, because once
        the parent exits the tree relationship is gone and ``taskkill /T`` can
        no longer reach the orphans.
        """
        if not self.is_running:
            return
        process_id = int(self.process.processId())
        descendants = _descendant_process_ids(process_id)
        self.process.terminate()
        if not self.process.waitForFinished(1500):
            # The tree is still intact while the parent lives, so take it down
            # in one call rather than orphaning the grandchild with kill().
            _force_kill(process_id, tree=True)
            self.process.waitForFinished(1000)
        for orphan in descendants:
            _force_kill(orphan, tree=True)

    def _restart_after_finish(self) -> None:
        try:
            self.process.finished.disconnect(self._restart_after_finish)
        except RuntimeError:
            pass
        QTimer.singleShot(100, self.start)

    def _kill_if_needed(self) -> None:
        if self.is_running:
            self.process.kill()

    def _read_output(self) -> None:
        raw = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            clean = line.strip()
            if clean:
                self.log_received.emit(clean)

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        if self._status not in {"stopping", "stopped"} and exit_code:
            self.log_received.emit(f"Mind engine stopped unexpectedly (exit code {exit_code}).")
            self._set_status("error")
        else:
            self._set_status("stopped")

    def _on_error(self, _error: QProcess.ProcessError) -> None:
        self.log_received.emit(self.process.errorString())
        self._set_status("error")

    def _set_status(self, status: str) -> None:
        if self._status == status:
            return
        self._status = status
        self.status_changed.emit(status)
