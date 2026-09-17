from __future__ import annotations

import unittest
import csv
import tempfile
from pathlib import Path

from tradetool.ui.company_names import company_name_for, load_company_names


class CompanyNamesTests(unittest.TestCase):
    def test_known_universe_names_are_available_without_runtime_legacy_dependency(self) -> None:
        names = load_company_names()
        self.assertGreaterEqual(len(names), 790)
        self.assertNotEqual(company_name_for('HUNT.OL'), 'HUNT.OL')
        self.assertNotEqual(company_name_for('AMD'), 'AMD')

    def test_unknown_ticker_falls_back_to_ticker(self) -> None:
        self.assertEqual(company_name_for('missing.ol'), 'MISSING.OL')

    def test_local_universe_metadata_precedes_bundled_display_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / 'universe.csv'
            fallback = Path(temp_dir) / 'fallback.csv'
            with source.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=['ticker', 'name'])
                writer.writeheader()
                writer.writerow({'ticker': 'AAA', 'name': 'Current company name'})
            with fallback.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=['ticker', 'name'])
                writer.writeheader()
                writer.writerow({'ticker': 'AAA', 'name': 'Old company name'})
                writer.writerow({'ticker': 'BBB', 'name': 'Fallback company name'})
            names = load_company_names((source,), fallback)
        self.assertEqual(company_name_for('AAA', names), 'Current company name')
        self.assertEqual(company_name_for('BBB', names), 'Fallback company name')
        self.assertEqual(company_name_for('CCC', names), 'CCC')

    def test_lookup_does_not_affect_order_or_risk_fields(self) -> None:
        from types import SimpleNamespace
        from tradetool.ui.v1_screener_adapter import v1_screener_table_rows

        result = SimpleNamespace(top_candidates=(
            {'ticker': 'HUNT.OL', 'incumbent_rank': 1, 'relative_strength_6m': 0.2, 'risk_level': 'HIGH'},
            {'ticker': 'AMD', 'incumbent_rank': 2, 'relative_strength_6m': 0.1, 'risk_level': 'LOW'},
        ), eligible_universe=())
        rows = v1_screener_table_rows(result)
        self.assertEqual([row['Ticker'] for row in rows], ['HUNT.OL', 'AMD'])
        self.assertEqual([row['Rang'] for row in rows], [1, 2])
        self.assertEqual([row['Risiko'] for row in rows], ['HIGH', 'LOW'])
        self.assertNotIn('Risikotagger', rows[0])


if __name__ == '__main__':
    unittest.main()
