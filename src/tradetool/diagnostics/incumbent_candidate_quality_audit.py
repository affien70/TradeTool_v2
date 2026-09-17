from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from tradetool.ranking.incumbent_screener import IncumbentScreenerResult, build_incumbent_screener
from tradetool.ui.company_names import company_name_for, load_company_names

TOP_COUNTS = (10, 20, 30)
RISK_LEVELS = ('LOW', 'MEDIUM', 'HIGH')
RISK_TAGS = (
    'very_low_liquidity', 'low_liquidity', 'high_volatility', 'deep_drawdown',
    'below_sma200', 'negative_3m_return', 'negative_3m_rs',
    'extreme_sma200_stretch', 'missing_risk_metric',
)
BUCKET_RULES = (
    ('Low-liquidity caution', 'low_liquidity or very_low_liquidity'),
    ('High-risk rebound', 'deep_drawdown'),
    ('Extended momentum', 'extreme_sma200_stretch'),
    ('Weak recent confirmation', 'negative_3m_return or negative_3m_rs'),
    ('Practical leader candidate', 'LOW risk and positive 3m return and RS'),
    ('Needs review', 'all remaining cases'),
)
REPORT_FILES = (
    'incumbent_candidate_quality_summary.md',
    'incumbent_candidate_quality_summary.json',
    'incumbent_top_candidates.csv',
    'incumbent_risk_distribution.csv',
    'incumbent_candidate_buckets.csv',
    'incumbent_decision.csv',
)


@dataclass(frozen=True, slots=True)
class CandidateQualityAudit:
    summary: dict[str, object]
    top_rows: tuple[dict[str, object], ...]
    risk_rows: tuple[dict[str, object], ...]
    bucket_rows: tuple[dict[str, object], ...]
    decision_rows: tuple[dict[str, object], ...]


def build_candidate_quality_audit(
    *,
    db_path: str | Path,
    universe_id: str,
    benchmark_ticker: str,
    as_of_date: date,
    data_source: str = 'yahoo',
) -> CandidateQualityAudit:
    db_path = Path(db_path).expanduser().resolve()
    incumbent = build_incumbent_screener(
        db_path=db_path,
        universe_db_path=db_path,
        universe_id=universe_id,
        benchmark_ticker=benchmark_ticker,
        as_of_date=as_of_date,
        data_source=data_source,
        top_n=max(TOP_COUNTS),
    )
    return audit_incumbent_result(incumbent)


def audit_incumbent_result(
    incumbent: IncumbentScreenerResult,
    *,
    names: Mapping[str, str] | None = None,
) -> CandidateQualityAudit:
    display_names = load_company_names() if names is None else dict(names)
    top_rows = tuple(_display_row(row, display_names) for row in incumbent.top_candidates)
    risk_rows: list[dict[str, object]] = []
    top_metrics: dict[str, dict[str, object]] = {}
    for top_n in TOP_COUNTS:
        scoped = top_rows[:top_n]
        levels = Counter(str(row['risk_level']) for row in scoped)
        tags = Counter(tag for row in scoped for tag in _tags(row))
        count = len(scoped)
        top_metrics[str(top_n)] = {
            'selected_count': count,
            'risk_level_counts': {level: levels[level] for level in RISK_LEVELS},
            'risk_tag_counts': {tag: tags[tag] for tag in RISK_TAGS},
            'high_risk_percent': 100.0 * levels['HIGH'] / count if count else None,
        }
        for level in RISK_LEVELS:
            risk_rows.append(_distribution_row(top_n, count, 'risk_level', level, levels[level]))
        for tag in RISK_TAGS:
            risk_rows.append(_distribution_row(top_n, count, 'risk_tag', tag, tags[tag]))

    mode_counts = _failure_mode_counts(top_rows)
    dominant = next(iter(sorted(mode_counts, key=lambda key: (-mode_counts[key], key))), None)
    if dominant is not None and mode_counts[dominant] == 0:
        dominant = None
    bucket_rows = tuple(
        {'incumbent_rank': row['incumbent_rank'], 'ticker': row['ticker'],
         'diagnostic_bucket': row['diagnostic_bucket'], 'bucket_reason': row['bucket_reason']}
        for row in top_rows
    )
    summary = {
        'baseline_id': incumbent.baseline_id,
        'universe_id': incumbent.universe_id,
        'universe_source': incumbent.universe_source,
        'benchmark_ticker': incumbent.benchmark_ticker,
        'as_of_date': incumbent.as_of_date,
        'effective_feature_date': incumbent.effective_feature_date,
        'data_source': incumbent.data_source,
        'eligible_ranked_count': incumbent.eligible_count,
        'top_n': top_metrics,
        'bucket_rules_in_priority_order': [
            {'bucket': label, 'condition': condition} for label, condition in BUCKET_RULES
        ],
        'failure_mode_counts_top_30': mode_counts,
        'dominant_failure_mode_top_30': dominant,
        'direct_practical_buy_list_validated': False,
        'recommended_use': 'high_rs_discovery_list',
        'next_challenger_hypothesis': 'test_practical_confirmation_and_risk_overlays_against_incumbent_on_holdout',
        'risk_tags_are_informational': True,
        'diagnostic_buckets_change_selection': False,
        'bucket_labels_are_tag_based_proxies_not_forward_validated': True,
        'output_files': list(REPORT_FILES),
    }
    decision_rows = ({
        'universe_id': incumbent.universe_id,
        'direct_practical_buy_list_validated': False,
        'recommended_use': summary['recommended_use'],
        'dominant_failure_mode_top_30': dominant or 'none',
        'next_challenger_hypothesis': summary['next_challenger_hypothesis'],
    },)
    return CandidateQualityAudit(summary, top_rows, tuple(risk_rows), bucket_rows, decision_rows)


def classify_diagnostic_bucket(row: Mapping[str, object]) -> tuple[str, str]:
    tags = _tags(row)
    if tags & {'very_low_liquidity', 'low_liquidity'}:
        return 'Low-liquidity caution', 'Existing liquidity risk tag.'
    if 'deep_drawdown' in tags:
        return 'High-risk rebound', 'High six-month RS with an existing deep-drawdown tag.'
    if 'extreme_sma200_stretch' in tags:
        return 'Extended momentum', 'Existing extreme-SMA200-stretch tag.'
    if tags & {'negative_3m_return', 'negative_3m_rs'}:
        return 'Weak recent confirmation', 'Negative recent return or relative-strength tag.'
    if str(row.get('risk_level')) == 'LOW' and _positive(row.get('return_3m')) and _positive(row.get('relative_strength_3m')):
        return 'Practical leader candidate', 'No risk flags and positive recent return and RS; diagnostic only.'
    return 'Needs review', 'No stronger diagnostic bucket applies.'


def write_candidate_quality_audit(*, result: CandidateQualityAudit, out_dir: str | Path) -> None:
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=False)
    (path / REPORT_FILES[0]).write_text(_markdown(result), encoding='utf-8')
    (path / REPORT_FILES[1]).write_text(json.dumps(result.summary, indent=2, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    _write_csv(path / REPORT_FILES[2], result.top_rows,
               ('incumbent_rank', 'company_name', 'ticker', 'relative_strength_6m', 'relative_strength_3m',
                'return_6m', 'return_3m', 'close', 'risk_level', 'risk_tags', 'diagnostic_bucket', 'bucket_reason'))
    _write_csv(path / REPORT_FILES[3], result.risk_rows,
               ('top_n', 'selected_count', 'kind', 'label', 'count', 'percent_of_selected'))
    _write_csv(path / REPORT_FILES[4], result.bucket_rows,
               ('incumbent_rank', 'ticker', 'diagnostic_bucket', 'bucket_reason'))
    _write_csv(path / REPORT_FILES[5], result.decision_rows,
               ('universe_id', 'direct_practical_buy_list_validated', 'recommended_use',
                'dominant_failure_mode_top_30', 'next_challenger_hypothesis'))


def _display_row(row: Mapping[str, object], names: dict[str, str]) -> dict[str, object]:
    bucket, reason = classify_diagnostic_bucket(row)
    ticker = str(row['ticker'])
    return {
        'incumbent_rank': row['incumbent_rank'],
        'company_name': company_name_for(ticker, names),
        'ticker': ticker,
        'relative_strength_6m': row.get('relative_strength_6m'),
        'relative_strength_3m': row.get('relative_strength_3m'),
        'return_6m': row.get('return_6m'),
        'return_3m': row.get('return_3m'),
        'close': row.get('close'),
        'risk_level': row.get('risk_level'),
        'risk_tags': row.get('risk_tags') or '',
        'diagnostic_bucket': bucket,
        'bucket_reason': reason,
    }


def _tags(row: Mapping[str, object]) -> set[str]:
    raw = row.get('risk_tags')
    if isinstance(raw, str):
        return {part for part in raw.split('|') if part}
    if isinstance(raw, (tuple, list, set)):
        return {str(part) for part in raw if part}
    return set()


def _positive(value: object) -> bool:
    try:
        number = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0.0


def _failure_mode_counts(rows: tuple[dict[str, object], ...]) -> dict[str, int]:
    tags_by_row = [_tags(row) for row in rows]
    return {
        'rebound': sum('deep_drawdown' in tags for tags in tags_by_row),
        'liquidity': sum(bool(tags & {'low_liquidity', 'very_low_liquidity'}) for tags in tags_by_row),
        'volatility': sum('high_volatility' in tags for tags in tags_by_row),
        'stretch': sum('extreme_sma200_stretch' in tags for tags in tags_by_row),
        'weak_recent_confirmation': sum(bool(tags & {'negative_3m_return', 'negative_3m_rs'}) for tags in tags_by_row),
    }


def _distribution_row(top_n: int, selected: int, kind: str, label: str, count: int) -> dict[str, object]:
    return {
        'top_n': top_n, 'selected_count': selected, 'kind': kind, 'label': label,
        'count': count, 'percent_of_selected': 100.0 * count / selected if selected else None,
    }


def _markdown(result: CandidateQualityAudit) -> str:
    summary = result.summary
    lines = [
        '# Incumbent candidate quality audit', '',
        f"- Universe: `{summary['universe_id']}`; benchmark: `{summary['benchmark_ticker']}`; as-of: `{summary['as_of_date']}`",
        f"- Ranked: {summary['eligible_ranked_count']}; incumbent: `{summary['baseline_id']}`",
        '- This is a high-RS discovery list, not a validated practical BUY list.',
        '- Risk flags and buckets are informational; no rank, eligibility, or selection changes.',
        '- Bucket labels are tag-based proxies, not proof of rebound, trade quality, or future returns.',
        '- Failure-mode counts can overlap because one stock can carry several risk tags.',
        '', '## Risk concentration', '', '| Top N | Selected | HIGH | HIGH % | Low liquidity | High volatility | Deep drawdown | Extreme stretch | Negative 3m return | Negative 3m RS |',
        '| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for top_n in TOP_COUNTS:
        metrics = summary['top_n'][str(top_n)]
        tags = metrics['risk_tag_counts']
        high_percent = metrics['high_risk_percent']
        lines.append(
            f"| {top_n} | {metrics['selected_count']} | {metrics['risk_level_counts']['HIGH']} | "
            f"{f'{high_percent:.1f}%' if high_percent is not None else 'n/a'} | "
            f"{tags['low_liquidity'] + tags['very_low_liquidity']} | {tags['high_volatility']} | "
            f"{tags['deep_drawdown']} | {tags['extreme_sma200_stretch']} | "
            f"{tags['negative_3m_return']} | {tags['negative_3m_rs']} |"
        )
    lines.extend(['', '## Diagnostic bucket rules (priority order)', ''])
    lines.extend(f"- **{rule['bucket']}**: {rule['condition']}." for rule in summary['bucket_rules_in_priority_order'])
    lines.extend(['', '## Top 30 in incumbent order', ''])
    for row in result.top_rows:
        lines.append(f"- {row['incumbent_rank']}. {row['company_name']} ({row['ticker']}): {row['diagnostic_bucket']}; {row['bucket_reason']}")
    lines.extend([
        '', '## Decision', '',
        f"- Dominant observed flag in Top 30: `{summary['dominant_failure_mode_top_30'] or 'none'}`.",
        '- Treat the incumbent as a high-RS discovery list; do not treat it as a direct practical BUY list.',
        f"- Next hypothesis: `{summary['next_challenger_hypothesis']}`. Validate on holdout before any policy change.",
    ])
    return '\n'.join(lines) + '\n'


def _write_csv(path: Path, rows: tuple[dict[str, object], ...], fieldnames: tuple[str, ...]) -> None:
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
