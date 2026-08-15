from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .paths import engine_path


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
        if not self.is_running:
            return
        self.process.terminate()
        if not self.process.waitForFinished(1500):
            self.process.kill()
            self.process.waitForFinished(1000)

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
