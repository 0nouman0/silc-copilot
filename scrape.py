"""Run the CHRIST scraper from any directory:

    python scrape.py            # collect / refresh the data (resumable)
    python scrape.py status     # is it running? what has been saved?
    python scrape.py --max-pages 300 --limit-programmes 10     # quick trial

Automatically uses the project's virtual environment (.venv) if it exists, so plain `python` is enough.
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PY = HERE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

if VENV_PY.exists() and Path(sys.executable).resolve() != VENV_PY.resolve():
    sys.exit(subprocess.call([str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]]))

sys.path.insert(0, str(HERE))
os.chdir(HERE)
from scraper.run import cli  # noqa: E402

sys.exit(cli(sys.argv[1:]))
