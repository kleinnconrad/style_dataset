"""Smoke test: every module in src/ imports with the locked dependencies.

Catches missing dependencies, import errors and incompatible dependency updates
before they reach the daily scraper workflow.
"""
import importlib
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"

MODULES = sorted(path.stem for path in SRC_DIR.glob("*.py") if path.stem != "__init__")


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module: str) -> None:
    """Imports a module from src/ to verify that it and its dependencies load.

    Args:
        module: Name of the module, importable because src/ is on the pytest path.
    """
    importlib.import_module(module)
