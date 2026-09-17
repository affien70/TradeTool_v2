from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tradetool.diagnostics.incumbent_candidate_quality_audit import (
    REPORT_FILES,
    audit_incumbent_result,
    build_candidate_quality_audit,
    classify_diagnostic_bucket,
    write_candidate_quality_audit,
)
from tradetool.diagnostics.incumbent_candidate_quality_audit_cli import (
    _latest_price_date,
    build_argument_parser,
    main,
)


def _incumbent_fixture():
    rows = []
    for rank in range(1, 36):
        tags = 'deep_drawdown|high_volatility' if rank <= 5 else 'deep_drawdown' if rank <= 10 else '' if rank <= 20 else 'negative_3m_rs'
        rows.append({
            'incumbent_rank': rank,
            'ticker': f'T{rank:02d}.OL',
            'relative_strength_6m': 1.0 - rank / 100,
            'relative_strength_3m': -0.1 if rank > 20 else 0.1,
            'return_6m': 0.2,
            'return_3m': 0.1,
            'close': 100.0,
            'risk_level': 'HIGH' if rank <= 10 else 'LOW' if rank <= 20 else 'MEDIUM',
            'risk_tags': tags,
        })
    return SimpleNamespace(
        baseline_id='incumbent_naive_rs_6m_top_10_v0',
        universe_id='NORWAY_V2', universe_source='universe_cache:NORWAY_V2',
        benchmark_ticker='OSEBX.OL', as_of_date='2026-09-17',
        effective_feature_date='2026-09-17', data_source='yahoo',
        eligible_count=35, top_candidates=tuple(rows[:30]),
    )


class CandidateQualityAuditTests(unittest.TestCase):
    def test_top_counts_and_order_match_incumbent_without_reranking(self) -> None:
        incumbent = _incumbent_fixture()
        audit = audit_incumbent_result(incumbent, names={'T01.OL': 'Example Company'})
        self.assertEqual([row['ticker'] for row in audit.top_rows], [row['ticker'] for row in incumbent.top_candidates])
        self.assertEqual([row['incumbent_rank'] for row in audit.top_rows], list(range(1, 31)))
        self.assertEqual([audit.summary['top_n'][str(n)]['selected_count'] for n in (10, 20, 30)], [10, 20, 30])
        self.assertEqual([audit.summary['top_n'][str(n)]['risk_level_counts']['HIGH'] for n in (10, 20, 30)], [10, 10, 10])
        self.assertEqual([audit.summary['top_n'][str(n)]['high_risk_percent'] for n in (10, 20, 30)], [100.0, 50.0, 100.0 / 3])
        self.assertEqual(audit.summary['top_n']['10']['risk_tag_counts']['high_volatility'], 5)
        self.assertEqual(audit.summary['top_n']['30']['risk_tag_counts']['negative_3m_rs'], 10)
        self.assertEqual(audit.summary['dominant_failure_mode_top_30'], 'rebound')

    def test_buckets_are_deterministic_and_only_diagnostic(self) -> None:
        cases = (
            ({'risk_tags': 'low_liquidity|deep_drawdown', 'risk_level': 'HIGH'}, 'Low-liquidity caution'),
            ({'risk_tags': 'deep_drawdown|extreme_sma200_stretch', 'risk_level': 'HIGH'}, 'High-risk rebound'),
            ({'risk_tags': 'extreme_sma200_stretch', 'risk_level': 'HIGH'}, 'Extended momentum'),
            ({'risk_tags': 'negative_3m_rs', 'risk_level': 'MEDIUM'}, 'Weak recent confirmation'),
            ({'risk_tags': '', 'risk_level': 'LOW', 'return_3m': 0.1, 'relative_strength_3m': 0.1}, 'Practical leader candidate'),
            ({'risk_tags': 'high_volatility', 'risk_level': 'HIGH'}, 'Needs review'),
        )
        for row, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify_diagnostic_bucket(row)[0], expected)
                self.assertEqual(classify_diagnostic_bucket(row)[0], expected)

    def test_company_name_is_display_only_and_missing_name_uses_ticker(self) -> None:
        incumbent = _incumbent_fixture()
        first = audit_incumbent_result(incumbent, names={'T01.OL': 'Current Name'})
        second = audit_incumbent_result(incumbent, names={'T01.OL': 'Renamed Company'})
        self.assertEqual(first.top_rows[0]['company_name'], 'Current Name')
        self.assertEqual(second.top_rows[0]['company_name'], 'Renamed Company')
        self.assertEqual(first.top_rows[1]['company_name'], 'T02.OL')
        self.assertEqual([row['ticker'] for row in first.top_rows], [row['ticker'] for row in second.top_rows])
        self.assertEqual(first.summary['top_n'], second.summary['top_n'])
        self.assertFalse(first.summary['diagnostic_buckets_change_selection'])
        self.assertTrue(first.summary['bucket_labels_are_tag_based_proxies_not_forward_validated'])
        self.assertFalse(first.summary['direct_practical_buy_list_validated'])

    def test_build_uses_app_db_read_only_for_prices_and_membership(self) -> None:
        incumbent = _incumbent_fixture()
        db_path = Path('/tmp/audit_read_only_app_db.sqlite')
        with patch('tradetool.diagnostics.incumbent_candidate_quality_audit.build_incumbent_screener', return_value=incumbent) as builder:
            result = build_candidate_quality_audit(
                db_path=db_path, universe_id='NORWAY_V2', benchmark_ticker='OSEBX.OL',
                as_of_date=date(2026, 9, 17),
            )
        self.assertEqual(result.summary['eligible_ranked_count'], 35)
        self.assertEqual(builder.call_args.kwargs['db_path'], db_path.resolve())
        self.assertEqual(builder.call_args.kwargs['universe_db_path'], db_path.resolve())
        self.assertEqual(builder.call_args.kwargs['top_n'], 30)

    def test_writer_is_append_only_and_has_required_outputs(self) -> None:
        result = audit_incumbent_result(_incumbent_fixture(), names={})
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / 'new_report'
            write_candidate_quality_audit(result=result, out_dir=out_dir)
            self.assertEqual({path.name for path in out_dir.iterdir()}, set(REPORT_FILES))
            summary = json.loads((out_dir / REPORT_FILES[1]).read_text(encoding='utf-8'))
            self.assertEqual(summary['recommended_use'], 'high_rs_discovery_list')
            with (out_dir / REPORT_FILES[2]).open(newline='', encoding='utf-8') as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 30)
            self.assertIn('company_name', rows[0])
            self.assertEqual(rows[0]['ticker'], 'T01.OL')
            with self.assertRaises(FileExistsError):
                write_candidate_quality_audit(result=result, out_dir=out_dir)

    def test_latest_date_query_does_not_write_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'prices.sqlite'
            with sqlite3.connect(db_path) as connection:
                connection.execute('CREATE TABLE price_history_v2 (price_date TEXT, data_source TEXT)')
                connection.execute('INSERT INTO price_history_v2 VALUES (?, ?)', ('2026-09-17', 'yahoo'))
            before = db_path.read_bytes()
            self.assertEqual(_latest_price_date(db_path, 'yahoo'), date(2026, 9, 17))
            self.assertEqual(db_path.read_bytes(), before)

    def test_cli_accepts_both_universes_and_writes_report(self) -> None:
        parser = build_argument_parser()
        self.assertEqual(parser.parse_args(['--universe-id', 'SP500']).universe_id, 'SP500')
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / 'sp500_report'
            with patch('tradetool.diagnostics.incumbent_candidate_quality_audit_cli.build_candidate_quality_audit',
                       return_value=audit_incumbent_result(_incumbent_fixture(), names={})) as builder:
                self.assertEqual(main([
                    '--db-path', str(Path(temp_dir) / 'app.sqlite'), '--universe-id', 'SP500',
                    '--as-of-date', '2026-09-17', '--out-dir', str(out_dir),
                ]), 0)
            self.assertEqual(builder.call_args.kwargs['benchmark_ticker'], '^GSPC')
            self.assertTrue((out_dir / REPORT_FILES[1]).is_file())


if __name__ == '__main__':
    unittest.main()
