from __future__ import annotations

from pathlib import Path

import streamlit as st

from tradetool.diagnostics.baseline_ranking import build_baseline_ranking_diagnostics
from tradetool.diagnostics.baseline_sanity import build_baseline_sanity_diagnostics
from tradetool.diagnostics.coverage import build_coverage_diagnostics
from tradetool.diagnostics.eligibility import build_eligibility_diagnostics
from tradetool.diagnostics.feature_readiness import build_feature_readiness_diagnostics
from tradetool.diagnostics.trade_policy import build_trade_policy_diagnostics
from tradetool.diagnostics.trade_policy_calibration import build_trade_policy_calibration_report
from tradetool.diagnostics.trade_policy_sanity import build_trade_policy_sanity_report


def _render_distribution_table(distribution: dict[str, int]) -> None:
    rows = [{'latest_data_date': key, 'ticker_count': value} for key, value in distribution.items()]
    if rows:
        st.dataframe(rows, use_container_width=True)


def render() -> None:
    st.title('Diagnostikk')
    st.write('Phase 3b gir kun manuell, lesebasert dekning- og structural-eligibility-diagnostikk.')
    st.info('Ingen produksjonsdatabase er koblet til. Oppgi kun en lokal kopi av SQLite-filen hvis du vil inspisere dekning eller structural eligibility.')

    database_path = st.text_input('Lokal kopi av SQLite-database', value='', placeholder='/tmp/portfolio_copy.sqlite')
    universe_id = st.text_input('Universe ID', value='NORWAY_V2')
    benchmark_ticker = st.text_input('Benchmark ticker (valgfritt)', value='^OSEAX')
    evidence_dir = st.text_input('Phase 1 evidence-dir (valgfritt)', value='evidence/v1_baseline/20260622T200335Z')
    price_table = st.text_input('Pris-tabell (valgfritt)', value='')

    if st.button('Inspiser skjemadekning'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane for å kjøre lesetilgangsdiagnostikk.')
            return

        try:
            result = build_coverage_diagnostics(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'UNSPECIFIED',
                price_table=price_table.strip() or None,
            )
        except Exception as exc:  # pragma: no cover - surfaced in UI only
            st.error(str(exc))
            return

        st.subheader('Dekningsrapport')
        st.json(result.to_dict())
        st.subheader('Siste datodispersjon')
        _render_distribution_table(dict(result.report.latest_data_date_distribution))

    if st.button('Kjør structural eligibility'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane for å kjøre structural-eligibility-diagnostikk.')
            return

        try:
            result = build_eligibility_diagnostics(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'UNSPECIFIED',
                price_table=price_table.strip() or None,
            )
        except Exception as exc:  # pragma: no cover - surfaced in UI only
            st.error(str(exc))
            return

        st.subheader('Structural eligibility')
        st.caption('Dette er bare strukturell eligibility for fremtidig rangering, ikke en screener eller kjøpsliste.')
        st.json(result.to_summary_dict())
        st.dataframe(
            [{'reason': key, 'count': value} for key, value in sorted(result.rejection_counts_by_reason.items())],
            use_container_width=True,
        )

    if st.button('Kjør feature readiness'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane for å kjøre feature-readiness-diagnostikk.')
            return

        try:
            result = build_feature_readiness_diagnostics(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'UNSPECIFIED',
                benchmark_ticker=benchmark_ticker.strip() or None,
                price_table=price_table.strip() or 'price_history',
            )
        except Exception as exc:  # pragma: no cover - surfaced in UI only
            st.error(str(exc))
            return

        st.subheader('Feature readiness')
        st.caption('Dette er bare rå feature readiness for fremtidig rangering, ikke ranking, trade signal eller kjøpsliste.')
        st.json(result.to_summary_dict())
        st.dataframe(
            [{'reason': key, 'count': value} for key, value in sorted(result.feature_missing_reason_counts.items())],
            use_container_width=True,
        )

    if st.button('Kjør baseline ranking'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane for å kjøre baseline-ranking-diagnostikk.')
            return

        try:
            result = build_baseline_ranking_diagnostics(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'UNSPECIFIED',
                benchmark_ticker=benchmark_ticker.strip() or None,
                price_table=price_table.strip() or 'price_history',
            )
        except Exception as exc:  # pragma: no cover - surfaced in UI only
            st.error(str(exc))
            return

        st.subheader('Baseline ranking')
        st.caption('Dette er bare diagnostisk baseline-ranking for senere sammenligning, ikke kjøpsråd eller produksjonspolicy.')
        st.json(result.to_summary_dict())
        st.dataframe([row.to_dict() for row in result.rows[:20]], use_container_width=True)

    if st.button('Kjør baseline sanity'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane for å kjøre baseline-sanity-diagnostikk.')
            return
        try:
            result = build_baseline_sanity_diagnostics(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'UNSPECIFIED',
                benchmark_ticker=benchmark_ticker.strip() or None,
                evidence_dir=Path(evidence_dir.strip() or 'evidence/v1_baseline/20260622T200335Z'),
                price_table=price_table.strip() or 'price_history',
            )
        except Exception as exc:  # pragma: no cover
            st.error(str(exc))
            return
        st.subheader('Baseline sanity')
        st.caption('Dette er bare en sanity-rapport for diagnostisk baseline-ranking, ikke kjøpsråd eller produksjonsgodkjenning.')
        st.json(result.to_summary_dict())
        st.dataframe([row.to_dict() for row in result.top20_rows], use_container_width=True)

    if st.button('Kjør trade-policy diagnostikk'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane for å kjøre trade-policy-diagnostikk.')
            return
        try:
            result = build_trade_policy_diagnostics(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'UNSPECIFIED',
                benchmark_ticker=benchmark_ticker.strip() or None,
                price_table=price_table.strip() or 'price_history',
            )
        except Exception as exc:  # pragma: no cover
            st.error(str(exc))
            return
        st.subheader('Trade-policy diagnostikk')
        st.caption('Dette er bare en praktisk diagnostisk overlay over baseline-ranking, ikke kjøpsråd eller produksjonsgodkjenning.')
        st.json(result.to_summary_dict())
        st.dataframe(
            [
                {
                    'raw_rank': row.raw_rank,
                    'ticker': row.ticker,
                    'raw_score': row.raw_score,
                    'trade_signal': row.trade_signal.value,
                    'policy_reasons': ', '.join(row.policy_reasons),
                    'policy_warnings': ', '.join(row.policy_warnings),
                }
                for row in result.rows[:20]
            ],
            use_container_width=True,
        )

    if st.button('Kjør trade-policy sanity'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane for å kjøre trade-policy-sanity.')
            return
        try:
            result = build_trade_policy_sanity_report(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'UNSPECIFIED',
                benchmark_ticker=benchmark_ticker.strip() or None,
                price_table=price_table.strip() or 'price_history',
            )
        except Exception as exc:  # pragma: no cover
            st.error(str(exc))
            return
        st.subheader('Trade-policy sanity')
        st.caption('Dette er bare en audit av trade-policy-diagnostikk, ikke kjøpsråd eller produksjonsgodkjenning.')
        st.json(result.to_summary_dict())
        st.dataframe(
            [
                {
                    'raw_rank': row.raw_rank,
                    'ticker': row.ticker,
                    'trade_signal': row.trade_signal.value,
                    'not_buy_explanation': '' if row.trade_signal.value == 'BUY' else ', '.join(row.policy_reasons),
                }
                for row in result.policy.rows[:20]
            ],
            use_container_width=True,
        )

    if st.button('Kjør trade-policy kalibrering'):
        if not database_path.strip():
            st.warning('Oppgi en lokal databasebane for å kjøre trade-policy-kalibrering.')
            return
        try:
            result = build_trade_policy_calibration_report(
                db_path=Path(database_path.strip()),
                universe_id=universe_id.strip() or 'UNSPECIFIED',
                benchmark_ticker=benchmark_ticker.strip() or None,
                price_table=price_table.strip() or 'price_history',
            )
        except Exception as exc:  # pragma: no cover
            st.error(str(exc))
            return
        st.subheader('Trade-policy kalibrering')
        st.caption('Dette er bare en scenario-rapport for threshold-kalibrering, ikke kjøpsråd eller produksjonsgodkjenning.')
        st.json(result.to_summary_dict())
        st.dataframe(
            [
                {
                    'scenario': scenario.name,
                    'signals': ', '.join(f'{signal}={count}' for signal, count in sorted(scenario.signal_counts.items())),
                    'positive_buy_watch': scenario.positive_buy_or_watch_count,
                    'bad_buy_watch': scenario.bad_buy_or_watch_count,
                }
                for scenario in result.scenario_summaries
            ],
            use_container_width=True,
        )


render()
