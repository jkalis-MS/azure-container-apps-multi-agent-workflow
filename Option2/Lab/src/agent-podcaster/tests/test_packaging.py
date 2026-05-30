"""Tests that validate packaging: every source module imports, every third-party
import is declared in pyproject.toml, and the Dockerfile copies all files."""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent  # agent-podcaster/
PYPROJECT = ROOT / "pyproject.toml"
DOCKERFILE = ROOT / "Dockerfile"

# Mapping from top-level import name to PyPI package name when they differ.
_IMPORT_TO_PACKAGE = {
    "dotenv": "python-dotenv",
    "bs4": "beautifulsoup4",
    "copilot": "github-copilot-sdk",
    "pydub": "pydub",
    "json_repair": "json-repair",
    "azure": "azure-storage-blob",
    "audioop_lts": "audioop-lts",
    "opentelemetry": "opentelemetry-api",
}

# Packages that are part of the Python stdlib — no pyproject entry needed.
_STDLIB = frozenset({
    "asyncio", "base64", "dataclasses", "datetime", "io", "json",
    "logging", "os", "pathlib", "re", "struct", "sys", "types",
    "typing", "unittest", "uuid", "wave", "collections", "functools",
    "importlib", "math", "hashlib", "copy", "itertools", "contextlib",
    "abc", "time", "__future__",
})


def _read_pyproject() -> str:
    return PYPROJECT.read_text(encoding="utf-8")


def _declared_dependencies() -> set[str]:
    """Return normalised (lower, hyphens→dashes) dependency names from pyproject."""
    text = _read_pyproject()
    # Matches lines like: "fastapi>=0.115",
    deps = set()
    for m in re.finditer(r'"([a-zA-Z0-9_-]+)', text):
        name = m.group(1).lower().replace("_", "-")
        deps.add(name)
    return deps


def _py_modules_from_pyproject() -> list[str]:
    """Return the py-modules list from pyproject.toml."""
    text = _read_pyproject()
    m = re.search(r'py-modules\s*=\s*\[([^\]]+)\]', text)
    assert m, "py-modules not found in pyproject.toml"
    return [s.strip().strip('"').strip("'") for s in m.group(1).split(",")]


def _source_py_files() -> list[Path]:
    """Return all .py files in the package root (not tests, not venv)."""
    files = []
    for f in ROOT.glob("*.py"):
        files.append(f)
    for f in ROOT.glob("tools/*.py"):
        files.append(f)
    return files


def _collect_imports(filepath: Path) -> set[str]:
    """Parse a Python file and return top-level imported module names."""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    return imports


# ---------------------------------------------------------------------------
# Test 1: Every source .py file can be imported (conftest stubs heavy deps)
# ---------------------------------------------------------------------------

class TestModuleImports:
    """Every module listed in py-modules must import without error."""

    # main and agent require full OTel context stubs beyond what conftest provides;
    # they are exercised via test_agent_podcaster.py integration tests instead.
    _SKIP_IMPORT = {"main", "agent"}

    @pytest.mark.parametrize("module_name", _py_modules_from_pyproject())
    def test_module_imports(self, module_name):
        if module_name in self._SKIP_IMPORT:
            pytest.skip(f"{module_name} tested via integration tests")
        __import__(module_name)

    def test_tools_package_imports(self):
        import tools
        import tools.fetch_content
        import tools.pronunciations


# ---------------------------------------------------------------------------
# Test 2: pyproject.toml dependencies cover all third-party imports
# ---------------------------------------------------------------------------

class TestDependencyCompleteness:
    """Every third-party import in source files must be declared in pyproject."""

    def test_all_third_party_imports_declared(self):
        declared = _declared_dependencies()
        source_files = _source_py_files()
        assert source_files, "No source files found"

        missing = []
        for filepath in source_files:
            for imp in _collect_imports(filepath):
                if imp in _STDLIB:
                    continue
                # Local modules (our own code)
                if (ROOT / f"{imp}.py").exists() or (ROOT / imp).is_dir():
                    continue
                # Map import name to package name
                pkg = _IMPORT_TO_PACKAGE.get(imp, imp).lower().replace("_", "-")
                if pkg not in declared:
                    missing.append(f"{filepath.name}: import '{imp}' → package '{pkg}'")

        assert not missing, (
            "Third-party imports not declared in pyproject.toml dependencies:\n"
            + "\n".join(f"  - {m}" for m in sorted(set(missing)))
        )


# ---------------------------------------------------------------------------
# Test 3: py-modules list covers all top-level .py files
# ---------------------------------------------------------------------------

class TestPyModulesList:
    """Every .py file in the root must be listed in py-modules."""

    def test_all_py_files_in_py_modules(self):
        declared = set(_py_modules_from_pyproject())
        actual = {f.stem for f in ROOT.glob("*.py") if f.stem != "__init__"}

        missing = actual - declared
        assert not missing, (
            f"Source files not listed in pyproject.toml py-modules: {missing}"
        )


# ---------------------------------------------------------------------------
# Test 4: Dockerfile copies all required source files
# ---------------------------------------------------------------------------

class TestDockerfile:
    """Dockerfile must COPY all source files that the app needs at runtime."""

    def test_dockerfile_copies_tools(self):
        content = DOCKERFILE.read_text(encoding="utf-8")
        assert "COPY tools/" in content, "Dockerfile must COPY tools/ directory"

    def test_dockerfile_copies_py_files(self):
        content = DOCKERFILE.read_text(encoding="utf-8")
        # Should copy *.py (all top-level Python files)
        assert "COPY *.py" in content or "COPY . " in content, (
            "Dockerfile must COPY *.py or COPY . to include all source files"
        )

    def test_dockerfile_copies_pyproject(self):
        content = DOCKERFILE.read_text(encoding="utf-8")
        assert "pyproject.toml" in content, (
            "Dockerfile must COPY pyproject.toml for pip install"
        )

    def test_dockerfile_installs_package(self):
        content = DOCKERFILE.read_text(encoding="utf-8")
        assert "pip install" in content, "Dockerfile must pip install the package"
