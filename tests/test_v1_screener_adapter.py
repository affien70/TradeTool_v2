from __future__ import annotations

from types import SimpleNamespace
import unittest

from tradetool.ui.v1_screener_adapter import (
    V1_SCREENER_TABLE_COLUMNS,
    resolve_v1_selected_ticker,
    select_v1_candidate,
    v1_candidate_detail_rows,
    v1_candidate_explanation,
    v1_screener_table_rows,
    v1_screener_ticker_options,
)


class V1ScreenerAdapterTests(unittest.TestCase):
    def test_formats_incumbent_output_without_changing_order(self) -> None:
        result = SimpleNamespace(
            top_candidates=(
                {
                    'ticker': 'BBB.OL',
                    'incumbent_rank': 1,
                    'relative_strength_6m': 0.42,
                    'relative_strength_3m': 0.12,
                    'return_6m': 0.38,
                    'return_3m': 0.08,
                    'close': 123.456,
                    'risk_level': 'HIGH',
                    'risk_tags': 'deep_drawdown|high_volatility',
                    'risk_explanation_no': 'Sterk RS, men høy risiko.',
                },
                {
                    'ticker': 'AAA.OL',
                    'incumbent_rank': 2,
                    'relative_strength_6m': 0.31,
                    'relative_strength_3m': 0.09,
                    'return_6m': 0.20,
                    'return_3m': 0.03,
                    'close': 45.0,
                    'risk_level': 'LOW',
                    'risk_tags': '',
                    'risk_explanation_no': 'Lav risikomerking.',
                },
            ),
            eligible_universe=(),
        )

        rows = v1_screener_table_rows(result)

        self.assertEqual(tuple(rows[0]), V1_SCREENER_TABLE_COLUMNS)
        self.assertEqual([row['Ticker'] for row in rows], ['BBB.OL', 'AAA.OL'])
        self.assertEqual(rows[0]['RS 6m'], '42.0%')
        self.assertEqual(rows[0]['Selskap'], 'BBB.OL')
        self.assertEqual([row['Risiko'] for row in rows], ['HIGH', 'LOW'])
        self.assertNotIn('Risikotagger', rows[0])
        self.assertNotIn('Forklaring', rows[0])

    def test_selected_ticker_matches_v1_table_flow(self) -> None:
        rows = [{'Ticker': 'BBB.OL'}, {'Ticker': 'AAA.OL'}]

        self.assertEqual(resolve_v1_selected_ticker(rows, selected_row_indexes=[1]), 'AAA.OL')
        self.assertEqual(resolve_v1_selected_ticker(rows, current_ticker='BBB.OL'), 'BBB.OL')
        self.assertEqual(resolve_v1_selected_ticker(rows, selected_ticker='AAA.OL'), 'AAA.OL')
        self.assertEqual(resolve_v1_selected_ticker(rows, selected_row_indexes=[0], selected_ticker='AAA.OL'), 'BBB.OL')
        self.assertEqual(resolve_v1_selected_ticker(rows, current_ticker='MISSING'), 'BBB.OL')
        self.assertEqual(v1_screener_ticker_options(rows), ['BBB.OL', 'AAA.OL'])

    def test_detail_and_explanation_are_separate_from_table(self) -> None:
        row = {
            'ticker': 'BBB.OL',
            'incumbent_rank': 1,
            'risk_tags': 'deep_drawdown|high_volatility',
            'risk_explanation_no': 'Sterk RS, men høy risiko.',
        }
        result = SimpleNamespace(eligible_universe=(row,))

        selected = select_v1_candidate(result, ticker='bbb.ol')
        detail = v1_candidate_detail_rows(selected)

        self.assertIs(selected, row)
        self.assertIn({'felt': 'Ticker', 'verdi': 'BBB.OL'}, detail)
        self.assertIn({'felt': 'Risikotagger', 'verdi': 'deep_drawdown, high_volatility'}, detail)
        self.assertEqual(v1_candidate_explanation(selected), 'Sterk RS, men høy risiko.')


if __name__ == '__main__':
    unittest.main()
