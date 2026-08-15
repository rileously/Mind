from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from . import __version__


UPDATE_REPOSITORY = "rileously/Mind"
UPDATE_API_URL = f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest"
MAX_UPDATE_BYTES = 250 * 1024 * 1024
TRUSTED_DOWNLOAD_HOSTS = ("github.com", "githubusercontent.com")


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    title: str
    notes: str
    page_url: str
    asset_url: str
    asset_name: str
    checksum_url: str


@dataclass(frozen=True)
class DownloadedUpdate:
    path: Path
    sha256: str
    size: int


class _SecureGitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_github_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SECURE_GITHUB_OPENER = urllib.request.build_opener(_SecureGitHubRedirectHandler())


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+][0-9A-Za-z.-]+)?\s*", value)
    if not match:
        raise ValueError(f"Invalid version: {value}")
    return tuple(int(part or 0) for part in match.groups())


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def parse_release(payload: object, current_version: str = __version__) -> ReleaseInfo | None:
    if not isinstance(payload, dict):
        raise UpdateError("The update service returned an invalid response.")
    if payload.get("draft") or payload.get("prerelease"):
        return None
    tag = str(payload.get("tag_name", "")).strip()
    try:
        version = ".".join(str(part) for part in version_tuple(tag))
    except ValueError as exc:
        raise UpdateError("The latest release has an invalid version tag.") from exc
    if not is_newer_version(version, current_version):
        return None

    page_url = str(payload.get("html_url", "")).strip()
    if page_url:
        _validate_github_url(page_url)
    candidates: list[tuple[int, str, str]] = []
    checksum_candidates: list[tuple[int, str]] = []
    for asset in payload.get("assets", []):
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", "")).strip()
        url = str(asset.get("browser_download_url", "")).strip()
        lowered = name.lower()
        if not url:
            continue
        if lowered in {"mind.exe.sha256", "mind.sha256"}:
            _validate_github_url(url)
            checksum_candidates.append((0, url))
            continue
        if lowered in {"sha256sums.txt", "checksums.txt"}:
            _validate_github_url(url)
            checksum_candidates.append((1, url))
            continue
        if not lowered.endswith(".exe"):
            continue
        if lowered in {"mindsetup.exe", "mind-setup.exe"}:
            score = 0
        elif lowered == "mind.exe":
            score = 1
        elif "mind" in lowered:
            score = 2
        else:
            continue
        _validate_github_url(url)
        candidates.append((score, name, url))
    candidates.sort(key=lambda item: (item[0], item[1].lower()))
    asset_name = candidates[0][1] if candidates else ""
    asset_url = candidates[0][2] if candidates else ""
    checksum_candidates.sort(key=lambda item: item[0])
    checksum_url = checksum_candidates[0][1] if checksum_candidates else ""
    return ReleaseInfo(
        version=version,
        tag=tag,
        title=str(payload.get("name") or f"Mind {version}").strip(),
        notes=str(payload.get("body") or "").strip(),
        page_url=page_url,
        asset_url=asset_url,
        asset_name=asset_name,
        checksum_url=checksum_url,
    )


def check_for_update(current_version: str = __version__, timeout: float = 10.0) -> ReleaseInfo | None:
    request = urllib.request.Request(
        UPDATE_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Mind/{current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with SECURE_GITHUB_OPENER.open(request, timeout=timeout) as response:
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code == 403:
            raise UpdateError("GitHub temporarily limited update checks. Try again later.") from exc
        raise UpdateError(f"The update service returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise UpdateError("Could not reach the update service. Check your internet connection.") from exc
    return parse_release(payload, current_version)


def updates_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "Mind" / "Updates"


def download_update(release: ReleaseInfo, timeout: float = 30.0) -> DownloadedUpdate:
    if not release.asset_url:
        raise UpdateError("This release does not include a Windows installer.")
    _validate_github_url(release.asset_url)
    directory = updates_dir()
    directory.mkdir(parents=True, exist_ok=True)
    safe_version = re.sub(r"[^0-9.]", "", release.version)
    destination = directory / f"Mind-{safe_version}.exe"
    partial = destination.with_suffix(".exe.part")
    request = urllib.request.Request(
        release.asset_url,
        headers={"Accept": "application/octet-stream", "User-Agent": f"Mind/{__version__}"},
    )
    digest = hashlib.sha256()
    size = 0
    try:
        with SECURE_GITHUB_OPENER.open(request, timeout=timeout) as response, partial.open("wb") as output:
            expected = int(response.headers.get("Content-Length", "0") or 0)
            if expected > MAX_UPDATE_BYTES:
                raise UpdateError("The update file is unexpectedly large.")
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPDATE_BYTES:
                    raise UpdateError("The update file is unexpectedly large.")
                output.write(chunk)
                digest.update(chunk)
        if size < 1024 * 1024:
            raise UpdateError("The downloaded update is incomplete.")
        with partial.open("rb") as executable:
            if executable.read(2) != b"MZ":
                raise UpdateError("The downloaded file is not a valid Windows executable.")
        actual_hash = digest.hexdigest().lower()
        if release.checksum_url:
            expected_hash = _download_checksum(release.checksum_url, timeout)
            if actual_hash != expected_hash:
                raise UpdateError("The update checksum did not match the published release.")
        os.replace(partial, destination)
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError("The update could not be downloaded.") from exc
    return DownloadedUpdate(destination, digest.hexdigest().upper(), size)


def _download_checksum(url: str, timeout: float) -> str:
    _validate_github_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": f"Mind/{__version__}"})
    try:
        with SECURE_GITHUB_OPENER.open(request, timeout=timeout) as response:
            content = response.read(16 * 1024).decode("ascii", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("The published update checksum could not be downloaded.") from exc
    match = re.search(r"\b([0-9a-fA-F]{64})\b", content)
    if not match:
        raise UpdateError("The published update checksum is invalid.")
    return match.group(1).lower()


def launch_update_installer(download_path: str | Path) -> None:
    if not getattr(sys, "frozen", False):
        raise UpdateError("Automatic installation is only available in the packaged Mind app.")
    source = Path(download_path).resolve()
    root = updates_dir().resolve()
    target = Path(sys.executable).resolve()
    if not source.is_file() or source.suffix.lower() != ".exe":
        raise UpdateError("The downloaded update is missing.")
    if not source.is_relative_to(root) or target.suffix.lower() != ".exe" or source == target:
        raise UpdateError("The update paths did not pass safety checks.")
    bundled_script = Path(__file__).resolve().parent / "install_update.ps1"
    if not bundled_script.is_file():
        raise UpdateError("The update installer helper is missing.")
    root.mkdir(parents=True, exist_ok=True)
    installer_script = root / "install_update.ps1"
    shutil.copy2(bundled_script, installer_script)
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(installer_script),
        "-MindProcessId",
        str(os.getpid()),
        "-MindParentProcessId",
        str(os.getppid()),
        "-Source",
        str(source),
        "-Target",
        str(target),
    ]
    env = os.environ.copy()
    env.pop("_MEIPASS2", None)
    env.pop("_MEIPASS", None)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    if "PATH" in env:
        paths = env["PATH"].split(os.pathsep)
        clean_paths = [p for p in paths if "_MEI" not in p]
        env["PATH"] = os.pathsep.join(clean_paths)
    try:
        subprocess.Popen(command, env=env, close_fds=True, creationflags=0x08000000)
    except OSError as exc:
        raise UpdateError("Windows could not start the update installer.") from exc


def _validate_github_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    trusted = any(host == suffix or host.endswith("." + suffix) for suffix in TRUSTED_DOWNLOAD_HOSTS)
    if parsed.scheme != "https" or not trusted:
        raise UpdateError("The update service returned an untrusted download address.")


class _CheckSignals(QObject):
    completed = Signal(bool, object)


class UpdateCheckWorker(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = _CheckSignals()

    def run(self) -> None:
        try:
            self.signals.completed.emit(True, check_for_update())
        except UpdateError as exc:
            self.signals.completed.emit(False, str(exc))


class _DownloadSignals(QObject):
    completed = Signal(bool, object)


class UpdateDownloadWorker(QRunnable):
    def __init__(self, release: ReleaseInfo):
        super().__init__()
        self.release = release
        self.signals = _DownloadSignals()

    def run(self) -> None:
        try:
            self.signals.completed.emit(True, download_update(self.release))
        except UpdateError as exc:
            self.signals.completed.emit(False, str(exc))
