from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tradetool.diagnostics.baseline_sanity import (
    BAD_FOCUS_TICKERS,
    DECISION_BLOCKED,
    DECISION_CONTINUE,
    DECISION_REVISE,
    POSITIVE_FOCUS_TICKERS,
    build_baseline_sanity_diagnostics,
    write_baseline_sanity_outputs,
)


def _insert_rows(connection: sqlite3.Connection, ticker: str, closes: list[float], *, last_date: date, volume: float = 100.0) -> None:
    rows = []
    start = last_date - timedelta(days=len(closes) - 1)
    for index, close_value in enumerate(closes):
        current_date = start + timedelta(days=index)
        rows.append((ticker, current_date.isoformat(), close_value, close_value + 1.0, close_value - 1.0, close_value, volume + index))
    connection.executemany('INSERT INTO price_history VALUES (?, ?, ?, ?, ?, ?, ?)', rows)


def _build_sanity_fixture_db(path: Path, *, include_bad: bool = False, include_short: bool = False) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            'CREATE TABLE price_history (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)'
        )
        connection.execute(
            'CREATE TABLE universe_cache (universe_key TEXT PRIMARY KEY, tickers_json TEXT, source_label TEXT, updated_at TEXT)'
        )
        last_date = date(2025, 1, 1) + timedelta(days=259)
        tickers = ['SUBC.OL', 'HAUTO.OL', 'MPCC.OL', 'FRO.OL', 'KIT.OL', 'ENDUR.OL', 'BWLPG.OL', 'VAR.OL', 'NHY.OL', 'AKRBP.OL']
        if include_bad:
            tickers.extend(['NBX.OL', 'ZENA.OL'])
        if include_short:
            tickers.append('PRS.OL')
        for offset, ticker in enumerate(tickers):
            slope = 1.4 - (offset * 0.05)
            if ticker in {'NBX.OL', 'ZENA.OL'}:
                slope = 2.0
            if ticker == 'PRS.OL':
                closes = [60.0 + index * 0.4 for index in range(200)]
            else:
                closes = [100.0 + index * slope for index in range(260)]
            _insert_rows(connection, ticker, closes, last_date=last_date, volume=2000.0 - offset * 50)
        benchmark = [300.0 + index * 0.4 for index in range(260)]
        _insert_rows(connection, '^OSEAX', benchmark, last_date=last_date, volume=3000.0)
        connection.execute(
            'INSERT INTO universe_cache VALUES (?, ?, ?, ?)',
            (
                'NORWAY_V2',
                json.dumps(tickers),
                'fixture',
                '2026-08-16T00:00:00Z',
            ),
        )


def _build_evidence_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / 'ose_method_comparison_top20.csv').write_text(
        'method,ticker\n'
        'main_momentum,SUBC.OL\n'
        'main_momentum,NHY.OL\n'
        'raw_ml,MPCC.OL\n'
        'parked_practical_first,ENDUR.OL\n',
        encoding='utf-8',
    )


class BaselineSanityDiagnosticsTests(unittest.TestCase):
    def test_cli_writer_creates_expected_files_and_summary_counts_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            evidence_dir = Path(temp_dir) / 'evidence'
            out_dir = Path(temp_dir) / 'out'
            _build_sanity_fixture_db(db_path)
            _build_evidence_dir(evidence_dir)
            result = build_baseline_sanity_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                evidence_dir=evidence_dir,
            )
            write_baseline_sanity_outputs(result=result, out_dir=out_dir)
            self.assertEqual(
                sorted(path.name for path in out_dir.iterdir()),
                ['bad_name_penalty.csv', 'baseline_sanity_summary.json', 'baseline_sanity_summary.md', 'baseline_top20.csv', 'focus_ticker_sanity.csv'],
            )
            summary = json.loads((out_dir / 'baseline_sanity_summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['ranked_count'], len(result.ranking.rows))

    def test_focus_ticker_file_includes_positive_and_bad_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            evidence_dir = Path(temp_dir) / 'evidence'
            _build_sanity_fixture_db(db_path)
            _build_evidence_dir(evidence_dir)
            result = build_baseline_sanity_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                evidence_dir=evidence_dir,
            )
            tickers = {row.ticker for row in result.focus_rows}
            self.assertTrue(set(POSITIVE_FOCUS_TICKERS).issubset(tickers))
            self.assertTrue(set(BAD_FOCUS_TICKERS).issubset(tickers))

    def test_focus_ticker_statuses_preserve_universe_and_eligibility_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            evidence_dir = Path(temp_dir) / 'evidence'
            _build_sanity_fixture_db(db_path, include_short=True)
            _build_evidence_dir(evidence_dir)
            result = build_baseline_sanity_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                evidence_dir=evidence_dir,
            )
            rows = {row.ticker: row for row in result.focus_rows}
            self.assertTrue(rows['PRS.OL'].present_in_universe)
            self.assertFalse(rows['PRS.OL'].structurally_eligible)
            self.assertFalse(rows['PRS.OL'].feature_complete)
            self.assertIn('structurally rejected', rows['PRS.OL'].note)

    def test_bad_name_penalty_detects_top10_top20_presence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            evidence_dir = Path(temp_dir) / 'evidence'
            _build_sanity_fixture_db(db_path, include_bad=True)
            _build_evidence_dir(evidence_dir)
            result = build_baseline_sanity_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                evidence_dir=evidence_dir,
            )
            self.assertGreaterEqual(result.bad_top10_count, 1)
            self.assertGreaterEqual(result.bad_top20_count, 1)

    def test_useful_candidate_coverage_counts_top10_top20(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            evidence_dir = Path(temp_dir) / 'evidence'
            _build_sanity_fixture_db(db_path)
            _build_evidence_dir(evidence_dir)
            result = build_baseline_sanity_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                evidence_dir=evidence_dir,
            )
            self.assertGreater(result.positive_top10_count, 0)
            self.assertGreater(result.positive_top20_count, 0)

    def test_decision_continue_when_no_bad_names_in_top20_and_comparison_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            evidence_dir = Path(temp_dir) / 'evidence'
            _build_sanity_fixture_db(db_path)
            _build_evidence_dir(evidence_dir)
            result = build_baseline_sanity_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                evidence_dir=evidence_dir,
            )
            self.assertEqual(result.decision, DECISION_CONTINUE)

    def test_decision_revise_when_bad_names_are_top20(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            evidence_dir = Path(temp_dir) / 'evidence'
            _build_sanity_fixture_db(db_path, include_bad=True)
            _build_evidence_dir(evidence_dir)
            result = build_baseline_sanity_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                evidence_dir=evidence_dir,
            )
            self.assertEqual(result.decision, DECISION_REVISE)

    def test_decision_blocked_when_phase1_comparison_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            evidence_dir = Path(temp_dir) / 'missing_evidence'
            _build_sanity_fixture_db(db_path)
            evidence_dir.mkdir(parents=True, exist_ok=True)
            result = build_baseline_sanity_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                evidence_dir=evidence_dir,
            )
            self.assertEqual(result.decision, DECISION_BLOCKED)

    def test_report_contains_no_trade_signal_candidate_type_or_ml_score(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'fixture.sqlite'
            evidence_dir = Path(temp_dir) / 'evidence'
            _build_sanity_fixture_db(db_path)
            _build_evidence_dir(evidence_dir)
            result = build_baseline_sanity_diagnostics(
                db_path=db_path,
                universe_id='NORWAY_V2',
                benchmark_ticker='^OSEAX',
                evidence_dir=evidence_dir,
            )
            out_dir = Path(temp_dir) / 'out'
            write_baseline_sanity_outputs(result=result, out_dir=out_dir)
            summary = json.loads((out_dir / 'baseline_sanity_summary.json').read_text(encoding='utf-8'))
            with (out_dir / 'baseline_top20.csv').open('r', encoding='utf-8', newline='') as handle:
                header = csv.DictReader(handle).fieldnames or []
            forbidden = {'trade_signal', 'candidate_type', 'ml_score'}
            self.assertTrue(forbidden.isdisjoint(summary.keys()))
            self.assertTrue(forbidden.isdisjoint(header))

    def test_no_streamlit_import_inside_diagnostics_module(self) -> None:
        module_source = Path('src/tradetool/diagnostics/baseline_sanity.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('streamlit', module_source)

    def test_no_holdings_signal_logic_is_added(self) -> None:
        module_source = Path('src/tradetool/diagnostics/baseline_sanity.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('tradetool.holdings', module_source)
        self.assertNotIn('holdings_signal', module_source)
