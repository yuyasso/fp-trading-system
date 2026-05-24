"""
Root conftest.py — project-wide pytest configuration.

Makes the scripts/ directory importable so that unit tests can import
helpers from scripts/run_is.py as `import run_is`.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add scripts/ to sys.path so tests can do `from run_is import ...`
sys.path.insert(0, str(Path(__file__).parent / "scripts"))
