"""Start the real Mind keyboard engine in a temporary profile and verify readiness."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mind.config_store import ConfigStore, DEFAULT_CONFIG
from mind.paths import engine_path


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        store = ConfigStore(Path(temporary) / "data", PROJECT_ROOT)
        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                "onboarding_complete": True,
                "provider_profile": "ollama",
                "provider": "custom",
                "model": "smoke-test-model",
                "endpoint": "http://localhost:11434/v1",
            }
        )
        store.save(config)
        store.ensure_commands()

        environment = os.environ.copy()
        environment["MIND_DATA_DIR"] = str(store.root)
        environment["MIND_ENGINE_EMBEDDED"] = "1"
        process = subprocess.Popen(
            [sys.executable, "-u", str(engine_path()), "--debug"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            output, _ = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            output, _ = process.communicate(timeout=3)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)

        if "Mind engine running" not in output:
            raise RuntimeError("The engine did not reach its ready state.\n" + output)
        print("Mind engine smoke test passed.")


if __name__ == "__main__":
    main()

