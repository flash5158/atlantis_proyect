"""Build the Atlantis runtime on the current OS (Linux, macOS or Windows)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
venv_python = root / "nexo" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python")
python = venv_python if venv_python.exists() else Path(sys.executable)
out = root / "desktop" / "extension" / "runtime"
out.mkdir(parents=True, exist_ok=True)
subprocess.run(
    [str(python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
     "--name", "atlantis-runtime", "--paths", str(root / "nexo"),
     "--hidden-import", "atlantis_hub.app", "--hidden-import", "atlantis_hub.models",
     str(root / "nexo" / "atlantis_runtime.py")], cwd=root, check=True
)
source = root / "dist" / ("atlantis-runtime.exe" if sys.platform == "win32" else "atlantis-runtime")
target = out / source.name
shutil.copy2(source, target)
if sys.platform != "win32":
    target.chmod(0o755)
print(f"Runtime {sys.platform} creado en {target}")
