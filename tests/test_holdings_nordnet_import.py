from __future__ import annotations

import io
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tradetool.holdings import (
    HoldingTransactionRecord,
    dry_run_nordnet_import,
    import_nordnet_transactions,
    initialize_holdings_schema,
    load_holding_transactions,
    parse_nordnet_export,
    reconstruct_position,
    upsert_holding_transaction,
)
from tradetool.holdings.storage import upsert_holding_transaction as storage_upsert_holding_transaction


HEADERS = (
    'Id', 'Bokføringsdag', 'Handelsdag', 'Oppgjørsdag', 'Portefølje',
    'Transaksjonstype', 'Verdipapir', 'ISIN', 'Antall', 'Kurs', 'Rente',
    'Totale Avgifter', 'Valuta', 'Beløp', 'Valuta', 'Kjøpsverdi', 'Valuta',
    'Resultat', 'Valuta', 'Totalt antall', 'Saldo', 'Vekslingskurs',
    'Transaksjonstekst', 'Makuleringsdato', 'Sluttseddelnummer',
    'Verifikationsnummer', 'Kurtasje', 'Valuta', 'Valutakurs', 'Innledende rente',
)
_FIELD_POSITIONS = {
    'Id': 0,
    'Handelsdag': 2,
    'Oppgjørsdag': 3,
    'Transaksjonstype': 5,
    'Verdipapir': 6,
    'ISIN': 7,
    'Antall': 8,
    'Kurs': 9,
    'Valuta': 12,
    'Beløp': 13,
    'Resultat': 17,
    'Transaksjonstekst': 22,
    'Kurtasje': 26,
}


def _export(rows: list[dict[str, str]], *, encoding: str = 'utf-16') -> bytes:
    lines = ['\t'.join(HEADERS)]
    for row in rows:
        values = [''] * len(HEADERS)
        for field_name, value in row.items():
            values[_FIELD_POSITIONS[field_name]] = value
        values[14] = 'IGNORED-CURRENCY-TWO'
        values[16] = 'IGNORED-CURRENCY-THREE'
        values[18] = 'IGNORED-CURRENCY-FOUR'
        values[27] = 'IGNORED-CURRENCY-FIVE'
        lines.append('\t'.join(values))
    text = '\n'.join(lines) + '\n'
    if encoding == 'utf-16':
        return b'\xff\xfe' + text.encode('utf-16-le')
    return text.encode(encoding)


def _row(**overrides: str) -> dict[str, str]:
    row = {
        'Id': 'broker-1', 'Handelsdag': '2025-01-02', 'Transaksjonstype': 'KJØPT',
        'Verdipapir': 'Synthetic Holding', 'ISIN': 'NO-SYNTH-01', 'Antall': '10',
        'Kurs': '100,00', 'Valuta': 'NOK', 'Beløp': '1 000,00',
        'Transaksjonstekst': 'Import note', 'Kurtasje': '5,00', 'Resultat': '', 'Oppgjørsdag': '2025-01-06',
    }
    row.update(overrides)
    return row


class NordnetImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.connection.close()

    def test_utf16_parser_maps_canonical_records_and_v1_types(self) -> None:
        parsed = parse_nordnet_export(_export([
            _row(),
            _row(Id='broker-2', Handelsdag='2025-01-03', Transaksjonstype='UTTAK VP', Antall='4'),
            _row(Id='broker-3', Handelsdag='2025-01-04', Transaksjonstype='INNLEGG OVERFØRING', Antall='2'),
            _row(Id='broker-4', Handelsdag='2025-01-05', Transaksjonstype='UTBYTTE', Antall='', Kurs='', Beløp='25'),
            _row(Id='broker-5', Handelsdag='2025-01-06', Transaksjonstype='PLATTFORMAVG KORR', Antall='', Kurs='', Beløp='-3'),
        ]))

        self.assertEqual(parsed.valid_mapped_row_count, 5)
        records = parsed.records
        self.assertEqual([record.transaction.transaction_type for record in records], ['KJØPT', 'SALG', 'INNLEGG OVERFØRING', 'UTBYTTE', 'PLATTFORMAVGIFT'])
        self.assertEqual([record.transaction.shares for record in records], [10.0, -4.0, 2.0, 0.0, 0.0])
        self.assertEqual(records[0].transaction.trade_date, '2025-01-02')
        self.assertEqual(records[0].transaction.settlement_date, '2025-01-06')
        self.assertEqual(records[0].transaction.nordnet_transaction_id, 'broker-1')
        self.assertEqual(records[0].transaction.ticker, None)
        self.assertEqual(records[0].note, 'Import note')
        self.assertEqual(records[0].fee, 5.0)

    def test_utf8_binary_file_falls_back_from_utf16(self) -> None:
        parsed = parse_nordnet_export(io.BytesIO(_export([_row()], encoding='utf-8')))

        self.assertEqual(parsed.valid_mapped_row_count, 1)
        self.assertFalse(parsed.has_errors)

    def test_production_header_has_five_duplicate_valuta_columns_without_overwriting_currency(self) -> None:
        parsed = parse_nordnet_export(_export([_row()]))

        self.assertEqual(len(HEADERS), 30)
        self.assertEqual(HEADERS.count('Valuta'), 5)
        self.assertEqual(parsed.records[0].transaction.currency, 'NOK')
        self.assertEqual(parsed.records[0].transaction.amount, 1000.0)

    def test_all_observed_transaction_types_use_the_approved_classification(self) -> None:
        classifications = {
            'KJØPT': 'POSITION_ADDITION',
            'INNLEGG OVERFØRING': 'POSITION_ADDITION',
            'SALG': 'POSITION_REDUCTION',
            'INNLØSN. UTTAK VP': 'POSITION_REDUCTION',
            'UTBYTTE': 'HOLDINGS_CASHFLOW',
            'PLATTFORMAVGIFT': 'HOLDINGS_CASHFLOW',
            'TILBAKEBET. FOND AVG': 'HOLDINGS_CASHFLOW',
            'PLATTFORMAVG KORR': 'HOLDINGS_CASHFLOW',
            'INNSKUDD': 'NON_HOLDINGS_ACCOUNT_CASHFLOW',
            'UTTAK INTERNT': 'NON_HOLDINGS_ACCOUNT_CASHFLOW',
            'Overføring via Trustly': 'NON_HOLDINGS_ACCOUNT_CASHFLOW',
            'TILDELING INNLEGG RE': 'ADMINISTRATIVE_IGNORE',
            'SLETTING UTTAK VP': 'ADMINISTRATIVE_IGNORE',
            'INNL. VP LIKVID': 'ADMINISTRATIVE_IGNORE',
            'BYTTE INNLEGG VP': 'ADMINISTRATIVE_IGNORE',
            'BYTTE UTTAK VP': 'ADMINISTRATIVE_IGNORE',
        }
        parsed = parse_nordnet_export(_export([
            _row(Id=f'broker-{index}', Transaksjonstype=transaction_type)
            for index, transaction_type in enumerate(classifications, start=1)
        ]))

        actual = {row.transaction_type: row.classification for row in parsed.classified_rows}
        self.assertEqual(actual, {transaction_type.upper(): classification for transaction_type, classification in classifications.items()})
        self.assertEqual(parsed.mapped_holdings_row_count, 8)
        self.assertEqual(parsed.ignored_non_holdings_cashflow_count, 3)
        self.assertEqual(parsed.ignored_administrative_count, 5)
        self.assertEqual(parsed.invalid_row_count, 0)
        self.assertEqual(parsed.unsupported_blocker_count, 0)

    def test_ignored_rows_do_not_block_dry_run_or_reach_holdings_storage(self) -> None:
        content = _export([
            _row(Id='purchase'),
            _row(Id='deposit', Handelsdag='', Transaksjonstype='INNSKUDD'),
            _row(Id='administrative', Handelsdag='', Transaksjonstype='BYTTE UTTAK VP'),
        ])
        initialize_holdings_schema(self.connection)
        preview = dry_run_nordnet_import(content, target_connection=self.connection)
        imported = import_nordnet_transactions(content, target_connection=self.connection, confirmed=True)

        self.assertFalse(preview.has_blocking_errors)
        self.assertEqual(preview.ignored_non_holdings_cashflow_count, 1)
        self.assertEqual(preview.ignored_administrative_count, 1)
        self.assertEqual(imported.written_row_count, 1)
        self.assertEqual(len(load_holding_transactions(self.connection)), 1)

    def test_dividend_remains_instrument_linked_cashflow_without_position_effect(self) -> None:
        content = _export([
            _row(Id='purchase'),
            _row(Id='dividend', Handelsdag='2025-01-05', Transaksjonstype='UTBYTTE', Antall='', Kurs='', Beløp='25,50'),
        ])
        parsed_dividend = next(
            record
            for record in parse_nordnet_export(content).records
            if record.transaction.transaction_type == 'UTBYTTE'
        )
        initialize_holdings_schema(self.connection)
        import_nordnet_transactions(content, target_connection=self.connection, confirmed=True)
        records = load_holding_transactions(self.connection)
        position = reconstruct_position(record.transaction for record in records)
        dividend = next(record for record in records if record.transaction.transaction_type == 'UTBYTTE')

        self.assertEqual(dividend.transaction.isin, 'NO-SYNTH-01')
        self.assertEqual(dividend.transaction.instrument_name, 'Synthetic Holding')
        self.assertEqual(dividend.transaction.amount, 25.5)
        self.assertEqual(dividend.transaction.currency, 'NOK')
        self.assertEqual(dividend.transaction, parsed_dividend.transaction)
        self.assertEqual(dividend.transaction.shares, 0.0)
        self.assertEqual(position.open_quantity, 10.0)
        self.assertEqual(position.cashflows[0].amount, 25.5)

    def test_partial_export_upserts_broker_id_overlaps_without_deleting_history(self) -> None:
        export_one = _export([
            _row(Id='A', Handelsdag='2025-01-01', Antall='10', Kurs='100,00', Beløp='1 000,00'),
            _row(Id='B', Handelsdag='2025-01-02', Antall='5', Kurs='110,00', Beløp='550,00'),
            _row(Id='C', Handelsdag='2025-01-03', Transaksjonstype='SALG', Antall='2', Kurs='120,00', Beløp='240,00'),
        ])
        export_two = _export([
            _row(Id='B', Handelsdag='2025-01-02', Antall='5', Kurs='111,00', Beløp='555,00'),
            _row(Id='C', Handelsdag='2025-01-03', Transaksjonstype='SALG', Antall='2', Kurs='120,00', Beløp='240,00'),
            _row(Id='D', Handelsdag='2025-01-04', Antall='3', Kurs='90,00', Beløp='270,00'),
            _row(Id='ignored', Handelsdag='', Transaksjonstype='INNSKUDD'),
        ])
        initialize_holdings_schema(self.connection)
        first = import_nordnet_transactions(export_one, target_connection=self.connection, confirmed=True)
        preview = dry_run_nordnet_import(export_two, target_connection=self.connection)
        second = import_nordnet_transactions(export_two, target_connection=self.connection, confirmed=True)
        records = load_holding_transactions(self.connection)
        records_by_broker_id = {record.transaction.nordnet_transaction_id: record for record in records}

        self.assertEqual(first.written_row_count, 3)
        self.assertEqual(preview.new_row_count, 1)
        self.assertEqual(preview.existing_idempotent_count, 1)
        self.assertEqual(preview.would_update_count, 1)
        self.assertEqual(preview.conflict_count, 0)
        self.assertEqual(preview.ignored_non_holdings_cashflow_count, 1)
        self.assertEqual(second.written_row_count, 2)
        self.assertEqual(set(records_by_broker_id), {'A', 'B', 'C', 'D'})
        self.assertEqual(records_by_broker_id['B'].transaction.price, 111.0)
        self.assertEqual(sum(record.transaction.nordnet_transaction_id == 'B' for record in records), 1)
        self.assertNotIn('ignored', records_by_broker_id)

        before_repeat = records
        repeat_preview = dry_run_nordnet_import(export_two, target_connection=self.connection)
        repeat = import_nordnet_transactions(export_two, target_connection=self.connection, confirmed=True)

        self.assertEqual(repeat_preview.new_row_count, 0)
        self.assertEqual(repeat_preview.existing_idempotent_count, 3)
        self.assertEqual(repeat_preview.would_update_count, 0)
        self.assertEqual(repeat.written_row_count, 0)
        self.assertEqual(load_holding_transactions(self.connection), before_repeat)

    def test_fallback_identity_overlap_is_idempotent_and_changed_key_is_new(self) -> None:
        original = _export([_row(Id='')])
        changed_fallback = _export([_row(Id='', Handelsdag='2025-01-03')])
        initialize_holdings_schema(self.connection)
        import_nordnet_transactions(original, target_connection=self.connection, confirmed=True)
        original_preview = dry_run_nordnet_import(original, target_connection=self.connection)
        original_records = load_holding_transactions(self.connection)
        repeated = import_nordnet_transactions(original, target_connection=self.connection, confirmed=True)
        changed_preview = dry_run_nordnet_import(changed_fallback, target_connection=self.connection)
        changed = import_nordnet_transactions(changed_fallback, target_connection=self.connection, confirmed=True)

        self.assertEqual(original_preview.existing_idempotent_count, 1)
        self.assertEqual(original_preview.would_update_count, 0)
        self.assertEqual(repeated.written_row_count, 0)
        self.assertEqual(load_holding_transactions(self.connection)[:1], original_records)
        self.assertEqual(changed_preview.new_row_count, 1)
        self.assertEqual(changed_preview.would_update_count, 0)
        self.assertEqual(changed.written_row_count, 1)
        self.assertEqual(len(load_holding_transactions(self.connection)), 2)

    def test_dry_run_is_read_only_and_detects_upload_duplicates(self) -> None:
        initialize_holdings_schema(self.connection)
        preview = dry_run_nordnet_import(_export([_row(), _row()]), target_connection=self.connection)

        self.assertEqual(preview.new_row_count, 1)
        self.assertEqual(preview.duplicate_upload_row_count, 1)
        self.assertEqual(preview.parse_result.duplicate_broker_identity_rows, 1)
        self.assertEqual(preview.parse_result.duplicate_fallback_identity_rows, 1)
        self.assertEqual(load_holding_transactions(self.connection), ())

    def test_confirmed_write_requires_schema_and_is_idempotent(self) -> None:
        content = _export([_row(), _row(Id='broker-2', Handelsdag='2025-01-03', Transaksjonstype='SALG', Antall='4')])
        not_confirmed = import_nordnet_transactions(content, target_connection=self.connection, confirmed=False)
        self.assertEqual(not_confirmed.written_row_count, 0)
        with self.assertRaisesRegex(ValueError, 'initialized explicitly'):
            import_nordnet_transactions(content, target_connection=self.connection, confirmed=True)

        initialize_holdings_schema(self.connection)
        first = import_nordnet_transactions(content, target_connection=self.connection, confirmed=True)
        second = import_nordnet_transactions(content, target_connection=self.connection, confirmed=True)
        records = load_holding_transactions(self.connection)
        position = reconstruct_position(record.transaction for record in records)

        self.assertEqual(first.written_row_count, 2)
        self.assertEqual(second.preview.existing_row_count, 2)
        self.assertEqual(len(records), 2)
        self.assertTrue(position.open)
        self.assertEqual(position.open_quantity, 6.0)
        self.assertEqual(position.remaining_cost_basis, 600.0)

    def test_confirmed_import_accepts_normal_writable_sqlite_connection(self) -> None:
        with sqlite3.connect(':memory:') as connection:
            initialize_holdings_schema(connection)

            result = import_nordnet_transactions(
                _export([_row()]),
                target_connection=connection,
                confirmed=True,
            )

            self.assertEqual(result.written_row_count, 1)
            self.assertEqual(len(load_holding_transactions(connection)), 1)

    def test_confirmed_import_rolls_back_all_new_rows_after_mid_import_failure(self) -> None:
        historical_export = _export([_row(Id='historical', Handelsdag='2025-01-01')])
        attempted_export = _export([
            _row(Id='new-1', Handelsdag='2025-01-02'),
            _row(Id='new-2', Handelsdag='2025-01-03'),
        ])
        initialize_holdings_schema(self.connection)
        import_nordnet_transactions(historical_export, target_connection=self.connection, confirmed=True)
        before_failure = load_holding_transactions(self.connection)
        write_attempts = 0

        def fail_after_first_write(connection, record, *, commit=True):
            nonlocal write_attempts
            write_attempts += 1
            if write_attempts == 2:
                raise RuntimeError('synthetic mid-import failure')
            return storage_upsert_holding_transaction(connection, record, commit=commit)

        with patch(
            'tradetool.holdings.nordnet_import.upsert_holding_transaction',
            side_effect=fail_after_first_write,
        ):
            with self.assertRaisesRegex(RuntimeError, 'synthetic mid-import failure'):
                import_nordnet_transactions(attempted_export, target_connection=self.connection, confirmed=True)

        after_failure = load_holding_transactions(self.connection)
        self.assertEqual(after_failure, before_failure)
        self.assertEqual(len(after_failure), 1)
        self.assertEqual(after_failure[0].transaction.nordnet_transaction_id, 'historical')

        successful_retry = import_nordnet_transactions(
            attempted_export,
            target_connection=self.connection,
            confirmed=True,
        )

        self.assertEqual(successful_retry.written_row_count, 2)
        self.assertEqual(
            {record.transaction.nordnet_transaction_id for record in load_holding_transactions(self.connection)},
            {'historical', 'new-1', 'new-2'},
        )

    def test_complete_export_recovers_from_single_preexisting_import_row(self) -> None:
        complete_export = _export([
            _row(Id='A', Handelsdag='2025-01-01'),
            _row(Id='B', Handelsdag='2025-01-02'),
            _row(Id='C', Handelsdag='2025-01-03'),
        ])
        first_row_only = _export([_row(Id='A', Handelsdag='2025-01-01')])
        initialize_holdings_schema(self.connection)
        import_nordnet_transactions(first_row_only, target_connection=self.connection, confirmed=True)

        preview = dry_run_nordnet_import(complete_export, target_connection=self.connection)
        recovered = import_nordnet_transactions(complete_export, target_connection=self.connection, confirmed=True)
        records = load_holding_transactions(self.connection)

        self.assertEqual(preview.existing_idempotent_count, 1)
        self.assertEqual(preview.new_row_count, 2)
        self.assertEqual(recovered.written_row_count, 2)
        self.assertEqual({record.transaction.nordnet_transaction_id for record in records}, {'A', 'B', 'C'})
        self.assertEqual(sum(record.transaction.nordnet_transaction_id == 'A' for record in records), 1)

    def test_invalid_rows_are_structured_and_block_confirmed_write(self) -> None:
        initialize_holdings_schema(self.connection)
        content = _export([
            _row(Id='valid'),
            _row(Id='invalid-date', Handelsdag='not-a-date'),
            _row(Id='invalid-number', Antall='not-a-number'),
            _row(Id='unsupported', Transaksjonstype='UKJENT'),
        ])
        preview = dry_run_nordnet_import(content, target_connection=self.connection)
        written = import_nordnet_transactions(content, target_connection=self.connection, confirmed=True)

        self.assertEqual(preview.parse_result.mapped_holdings_row_count, 1)
        self.assertEqual(preview.parse_result.invalid_row_count, 2)
        self.assertEqual(preview.parse_result.unsupported_blocker_count, 1)
        self.assertTrue(preview.has_blocking_errors)
        self.assertEqual(written.written_row_count, 0)
        self.assertEqual(load_holding_transactions(self.connection), ())

    def test_invalid_encoding_and_missing_headers_are_structured(self) -> None:
        invalid_encoding = parse_nordnet_export(b'\xff')
        missing_headers = parse_nordnet_export('Id\tHandelsdag\n1\t2025-01-02\n'.encode('utf-8'))

        self.assertEqual(invalid_encoding.issues[0].code, 'unsupported_encoding')
        self.assertEqual(missing_headers.issues[0].code, 'missing_required_columns')

    def test_dry_run_blocks_conflicting_populated_target_identities(self) -> None:
        initialize_holdings_schema(self.connection)
        candidate = parse_nordnet_export(_export([_row(Handelsdag='2025-02-01')])).records[0]
        broker_record = HoldingTransactionRecord.from_transaction(replace(
            candidate.transaction,
            trade_date='2025-01-01',
        ))
        fallback_record = HoldingTransactionRecord.from_transaction(replace(
            candidate.transaction,
            nordnet_transaction_id='other-broker-id',
        ))
        upsert_holding_transaction(self.connection, broker_record)
        upsert_holding_transaction(self.connection, fallback_record)

        preview = dry_run_nordnet_import(_export([_row(Handelsdag='2025-02-01')]), target_connection=self.connection)

        self.assertEqual(preview.conflict_row_count, 1)
        self.assertEqual(preview.rows[0].status, 'conflict')
        self.assertTrue(preview.has_blocking_errors)
        self.assertEqual(preview.issues[0].code, 'conflicting_target_identity')

    def test_service_has_no_ui_v1_market_or_schema_creation_dependencies(self) -> None:
        source = Path('src/tradetool/holdings/nordnet_import.py').read_text(encoding='utf-8')
        self.assertNotIn('streamlit', source)
        self.assertNotIn('tradetool.holdings.v1_import', source)
        self.assertNotIn('tradetool.market', source)
        self.assertNotIn('initialize_holdings_schema', source)
        self.assertNotIn('screener', source)


if __name__ == '__main__':
    unittest.main()