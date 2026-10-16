"""Isolated release entry point, independent of global Python packages."""

from pathlib import Path
import sys

directory = Path(__file__).resolve().parent
sys.path.insert(0, str(directory.parent / "vendor"))
sys.path.insert(0, str(directory))

from hoomy_agent.__main__ import main

raise SystemExit(main())
