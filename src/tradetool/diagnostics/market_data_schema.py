from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradetool.data.market_data_schema import (
    MarketDataSchemaInspectionResult,
    V2_PRICE_TABLE_NAME,
    initialize_market_data_schema,
    inspect_market_data_schema,
)


@dataclass(frozen=True, slots=True)
class MarketDataSchemaReport:
    inspection: MarketDataSchemaInspectionResult
    init_schema_requested: bool
    generated_at_utc: str

    def to_summary_dict(self) -> dict[str, object]:
        return {
            'db_path': str(self.inspection.db_path),
            'table_name': self.inspection.table_name,
            'schema_initialized': self.inspection.schema_initialized,
            'init_schema_requested': self.init_schema_requested,
            'row_count': self.inspection.row_count,
            'column_count': len(self.inspection.columns),
            'columns': [column.to_dict() for column in self.inspection.columns],
            'indexes': list(self.inspection.indexes),
            'generated_at_utc': self.generated_at_utc,
        }


def build_market_data_schema_report(*, db_path: str | Path, init_schema: bool) -> MarketDataSchemaReport:
    inspection = initialize_market_data_schema(db_path) if init_schema else inspect_market_data_schema(db_path)
    return MarketDataSchemaReport(
        inspection=inspection,
        init_schema_requested=init_schema,
        generated_at_utc=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def write_market_data_schema_report(*, report: MarketDataSchemaReport, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'market_data_schema_summary.json').write_text(
        json.dumps(report.to_summary_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    (out_dir / 'market_data_schema_summary.md').write_text(_render_markdown(report), encoding='utf-8')
    with (out_dir / 'market_data_schema_columns.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=['name', 'declared_type', 'not_null', 'default_value', 'primary_key_position'],
        )
        writer.writeheader()
        for column in report.inspection.columns:
            writer.writerow(column.to_dict())


def _render_markdown(report: MarketDataSchemaReport) -> str:
    lines = [
        '# Market Data Schema Summary',
        '',
        '## Scope',
        '',
        f'- DB path: {report.inspection.db_path}',
        f'- Init schema requested: {report.init_schema_requested}',
        f'- Table name: {report.inspection.table_name}',
        f'- Schema initialized: {report.inspection.schema_initialized}',
        f'- Row count: {report.inspection.row_count}',
        '',
        '## Storage contract',
        '',
        f'- Table `{V2_PRICE_TABLE_NAME}` separates raw OHLC from adjusted close.',
        '- No market rows are inserted by this CLI.',
        '- No network fetch is performed by this CLI.',
        '- Legacy `price_history` remains unchanged and read-only.',
        '',
        '## Columns',
        '',
    ]
    for column in report.inspection.columns:
        lines.append(
            f'- {column.name}: {column.declared_type or "TEXT"} | not_null={column.not_null} | pk={column.primary_key_position}'
        )
    if not report.inspection.columns:
        lines.append('- No schema columns found.')
    lines.extend(['', '## Indexes', ''])
    if report.inspection.indexes:
        for index_name in report.inspection.indexes:
            lines.append(f'- {index_name}')
    else:
        lines.append('- No indexes detected.')
    return '\n'.join(lines) + '\n'
