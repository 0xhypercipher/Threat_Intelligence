import json
import os
import tempfile
from typing import Any


def atomic_write(path: str, data: str) -> None:
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_state(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"completed": {}, "pending_crawls": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"completed": {}, "pending_crawls": {}}


def save_state(path: str, state: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2))
