"""Shared pytest fixtures and helpers.

Two of the project's source files use hyphens in their names
(``routing-logger.py`` and ``sub-agent-interface.py``), which makes them
impossible to import with a plain ``import`` statement. The helper below loads
them by file path so the pure-logic helpers inside can be unit-tested.

All modules exercised by the test suite import their heavier dependencies
(PyYAML, requests) lazily, so importing them here pulls in nothing beyond the
standard library.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Make the underscore-named modules (router, shell_ghost) importable.
sys.path.insert(0, str(REPO_ROOT))


def load_module_by_path(module_name: str, filename: str):
    """Load a module from a file path (used for hyphenated filenames)."""
    path = REPO_ROOT / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
