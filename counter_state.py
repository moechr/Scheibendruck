"""Persistente, benannte Zaehler (z.B. Starter/Serie/Schuss-Nummer)."""
import json
from pathlib import Path
from typing import Dict, Union


class MultiCounterState:
    """Verwaltet mehrere benannte Zaehlerstaende in einer gemeinsamen JSON-Datei."""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    def _read_all(self) -> Dict[str, int]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {k: int(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError):
            return {}

    def read(self, name: str, default: int) -> int:
        return self._read_all().get(name, default)

    def write(self, name: str, value: int) -> None:
        self.write_many({name: value})

    def write_many(self, values: Dict[str, int]) -> None:
        data = self._read_all()
        data.update(values)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
