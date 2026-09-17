from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_UNIVERSE_FILES = (
    _REPO_ROOT.parent / 'TradeTool' / 'norway_tickers.csv',
    _REPO_ROOT.parent / 'TradeTool' / 'sp500_tickers.csv',
)
NAME_FALLBACK = _REPO_ROOT / 'src' / 'tradetool' / 'universe' / 'resources' / 'company_names.csv'


@lru_cache(maxsize=8)
def load_company_names(
    source_files: tuple[Path, ...] = LOCAL_UNIVERSE_FILES,
    fallback_file: Path = NAME_FALLBACK,
) -> dict[str, str]:
    # Names are optional display metadata. Never use this mapping for universe membership.
    names: dict[str, str] = {}
    for path in source_files:
        names.update(_read_name_csv(path))
    for ticker, name in _read_name_csv(fallback_file).items():
        names.setdefault(ticker, name)
    return names


def _read_name_csv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    with path.open(newline='', encoding='utf-8-sig') as source:
        return {
            ticker: name
            for row in csv.DictReader(source)
            if (ticker := str(row.get('ticker') or '').strip().upper())
            if (name := str(row.get('name') or '').strip())
        }


def company_name_for(ticker: str, names: dict[str, str] | None = None) -> str:
    cleaned = str(ticker or '').strip().upper()
    return (load_company_names() if names is None else names).get(cleaned, cleaned)
