from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

V1_SCREENER_TABLE_COLUMNS = (
    'Rang',
    'Ticker',
    'RS 6m',
    'RS 3m',
    '6m %',
    '3m %',
    'Siste kurs',
    'Risiko',
    'Risikotagger',
)


def v1_screener_table_rows(result) -> list[dict[str, object]]:
    return [_display_row(row) for row in result.top_candidates]


def v1_screener_eligible_rows(result) -> list[dict[str, object]]:
    return [_display_row(row) for row in result.eligible_universe]


def v1_screener_ticker_options(table_rows: Sequence[Mapping[str, object]]) -> list[str]:
    return [
        str(row.get('Ticker') or '').strip().upper()
        for row in table_rows
        if str(row.get('Ticker') or '').strip()
    ]


def resolve_v1_selected_ticker(
    table_rows: Sequence[Mapping[str, object]],
    *,
    current_ticker: str = '',
    selected_row_indexes: Sequence[int] | None = None,
    selected_ticker: str = '',
) -> str:
    tickers = v1_screener_ticker_options(table_rows)
    if not tickers:
        return ''

    if selected_row_indexes:
        selected_index = int(selected_row_indexes[0])
        if 0 <= selected_index < len(tickers):
            return tickers[selected_index]

    direct_ticker = str(selected_ticker or '').strip().upper()
    if direct_ticker in tickers:
        return direct_ticker

    cleaned_current = str(current_ticker or '').strip().upper()
    if cleaned_current in tickers:
        return cleaned_current
    return tickers[0]


def select_v1_candidate(result, *, ticker: str) -> Mapping[str, object]:
    cleaned_ticker = str(ticker or '').strip().upper()
    for row in result.eligible_universe:
        if str(row.get('ticker') or '').strip().upper() == cleaned_ticker:
            return row
    raise ValueError(f'Ticker not found in current incumbent screener result: {ticker}')


def v1_candidate_detail_rows(row: Mapping[str, object]) -> list[dict[str, object]]:
    return [
        {'felt': 'Ticker', 'verdi': row.get('ticker')},
        {'felt': 'Incumbent-rang', 'verdi': row.get('incumbent_rank')},
        {'felt': 'Siste featuredato', 'verdi': row.get('latest_feature_date')},
        {'felt': 'Siste prisdato', 'verdi': row.get('latest_price_date')},
        {'felt': '6m relativ styrke', 'verdi': _format_percent(row.get('relative_strength_6m'))},
        {'felt': '3m relativ styrke', 'verdi': _format_percent(row.get('relative_strength_3m'))},
        {'felt': '6m avkastning', 'verdi': _format_percent(row.get('return_6m'))},
        {'felt': '3m avkastning', 'verdi': _format_percent(row.get('return_3m'))},
        {'felt': 'Siste kurs', 'verdi': _format_number(row.get('close'), decimals=2)},
        {'felt': 'Over SMA200', 'verdi': _format_bool(row.get('above_sma200'))},
        {'felt': 'Likviditet 20d', 'verdi': _format_number(row.get('average_traded_value_20'), decimals=0)},
        {'felt': 'Drawdown 252d', 'verdi': _format_percent(row.get('drawdown_252'))},
        {'felt': 'Volatilitet 63d', 'verdi': _format_percent(row.get('volatility_63'))},
        {'felt': 'MA200-avstand', 'verdi': _format_percent(row.get('distance_to_sma200'))},
        {'felt': 'Risikoklasse', 'verdi': row.get('risk_level')},
        {'felt': 'Risikotagger', 'verdi': _format_tags(row.get('risk_tags'))},
    ]


def v1_candidate_explanation(row: Mapping[str, object]) -> str:
    explanation = str(row.get('risk_explanation_no') or '').strip()
    return explanation or 'Ingen egen risikoforklaring tilgjengelig.'


def v1_summary_rows(result) -> list[dict[str, object]]:
    return [
        {'felt': 'Univers', 'verdi': result.universe_id},
        {'felt': 'Benchmark', 'verdi': result.benchmark_ticker},
        {'felt': 'Effektiv dato', 'verdi': result.effective_feature_date or result.as_of_date},
        {'felt': 'Rangert', 'verdi': result.eligible_count},
        {'felt': 'Valgt', 'verdi': result.selected_count},
    ]


def _display_row(row: Mapping[str, object]) -> dict[str, object]:
    return {
        'Rang': row.get('incumbent_rank'),
        'Ticker': row.get('ticker'),
        'RS 6m': _format_percent(row.get('relative_strength_6m')),
        'RS 3m': _format_percent(row.get('relative_strength_3m')),
        '6m %': _format_percent(row.get('return_6m')),
        '3m %': _format_percent(row.get('return_3m')),
        'Siste kurs': _format_number(row.get('close'), decimals=2),
        'Risiko': row.get('risk_level'),
        'Risikotagger': _format_tags(row.get('risk_tags')),
    }


def _format_number(value: object, *, decimals: int) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ''
    if not math.isfinite(numeric):
        return ''
    return f'{numeric:.{decimals}f}'


def _format_percent(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ''
    if not math.isfinite(numeric):
        return ''
    return f'{numeric:.1%}'


def _format_bool(value: object) -> str:
    if value is True:
        return 'Ja'
    if value is False:
        return 'Nei'
    return ''


def _format_tags(value: object) -> str:
    return str(value or '').replace('|', ', ')
