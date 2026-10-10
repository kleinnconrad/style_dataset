"""Smoke test: every script in scripts/ imports with the locked dependencies.

The scripts add src/ to the import path themselves, so this also checks that
their imports of the pipeline modules resolve.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"

SCRIPTS = sorted(SCRIPTS_DIR.glob("*.py"))


@pytest.mark.parametrize("script", SCRIPTS, ids=[path.stem for path in SCRIPTS])
def test_script_imports(script: Path) -> None:
    """Imports a script as a module without running its main function.

    Args:
        script: Path of the script.
    """
    spec = importlib.util.spec_from_file_location(f"scripts_{script.stem}", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
