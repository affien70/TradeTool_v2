from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppSettings:
    project_root: Path
    reports_directory: Path
    evidence_directory: Path
    specification_directory: Path
    default_ui_language: str
    application_name: str
    contract_version: str


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def get_settings(project_root: Path | None = None) -> AppSettings:
    root = (project_root or _default_project_root()).resolve()
    return AppSettings(
        project_root=root,
        reports_directory=root / 'reports',
        evidence_directory=root / 'evidence',
        specification_directory=root / 'docs' / 'specification',
        default_ui_language='no',
        application_name='TradeTool v2',
        contract_version='v2-phase3a',
    )
