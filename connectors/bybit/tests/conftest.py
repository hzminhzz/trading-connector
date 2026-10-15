# pyright: reportMissingImports=false
# pyright: reportUnknownMemberType=false, reportUnknownParameterType=false
# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false
from __future__ import annotations

import os
import pytest


@pytest.fixture(autouse=True)
def _isolate_bybit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for key in list(os.environ):
        if key.startswith("BYBIT_"):
            monkeypatch.delenv(key, raising=False)
