import importlib

import pytest

PACKAGE_ROOTS = (
    "orchestrator",
    "server",
    "tools",
    "voice",
    "workflows",
)


@pytest.mark.parametrize("package_name", PACKAGE_ROOTS)
def test_package_root_imports(package_name: str) -> None:
    module = importlib.import_module(package_name)

    assert module.__name__ == package_name
