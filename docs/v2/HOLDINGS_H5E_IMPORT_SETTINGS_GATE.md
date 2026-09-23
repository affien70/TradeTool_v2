# Holdings H5e Import and Settings Gate

## Decision

`BLOCKED_BY_MISSING_NORDNET_IMPORT_API`

V2 has explicit schema, transaction-upsert, identity, and typed-settings
primitives. It has no raw Nordnet export parser, Nordnet dry-run result, or
Nordnet-specific confirmed-import service. Building the upload in Streamlit now
would put decoding, column mapping, transaction classification, identity, and
duplicate handling in the UI.

## Existing V2 Capabilities

- `HoldingTransaction`, `fallback_transaction_key`, and `transaction_identity`
  own canonical transactions and identity. Broker ID has priority; fallback is
  derived from trade/settlement date, ISIN/ticker/instrument, type, shares, and
  currency, not price or amount.
- `HoldingTransactionRecord.from_transaction` creates a persistence record and
  validates its deterministic fallback key.
- `upsert_holding_transaction(connection, record)` is an explicit write
  primitive. Its unique broker-ID and fallback-key constraints make repeated
  records update one logical transaction. It is not a raw-file import service.
- `dry_run_v1_holdings_import` and `import_v1_holdings` provide counts and an
  idempotent write path only for a read-only V1 SQLite database. They do not
  accept uploaded Nordnet CSV/TSV bytes.
- No V2 source or test contains UTF-16 Nordnet decoding, tab-separated Nordnet
  parsing, Nordnet header mapping, or raw-upload handling.

## Existing Settings Capabilities

- `HoldingSettings` has deterministic defaults: `1 år`, six-month RS,
  `sell_fast_sma_days=100`, and `norway_benchmark_id='OSEBX.OL'`.
- `load_holding_settings(connection)` returns those defaults when the settings
  table or global row is absent.
- `save_holding_settings(connection, settings)` is an explicit typed singleton
  upsert and returns the stored typed settings. It requires an initialized
  Holdings schema; it is the existing engine-owned persistence boundary, not
  direct UI SQL.
- There is no higher-level page/application write facade. A future UI must pass
  a typed `HoldingSettings` to the engine persistence boundary and must not
  issue SQL itself.

## Schema Initialization Contract

`initialize_holdings_schema(connection)` is public, explicit, idempotent
`CREATE ... IF NOT EXISTS` setup for only the V2 Holdings tables and indexes.
It requires a caller-supplied writable connection and has no V1 dependency.
It is suitable only for an explicit, user-confirmed administration/import
action. Normal Beholdning rendering must continue to use read-only access and
must never invoke it.

## Verified V1 Nordnet File Behavior

The V1 parser reads tab-separated bytes, tries UTF-16 first and UTF-8 on a
decode failure. Required headers are `Id`, `Handelsdag`, `Transaksjonstype`,
`Verdipapir`, `ISIN`, `Antall`, `Kurs`, `Valuta`, `Beløp`, and
`Transaksjonstekst`. `Kurtasje` is optional and defaults to zero; `Resultat`
is optional. Settlement date is read only when one of `Oppgjørsdag`,
`Oppgjørsdato`, `Settlement date`, or `Settlement Date` is present.

It retains only known transaction types, maps `Id` to broker transaction ID,
normalizes transaction type/currency, sanitizes ISIN, parses Norwegian numeric
text by removing spaces and changing comma decimal separators, and maps dates
to ISO text. Invalid numeric fields become zero in its output; invalid trade
dates become `1970-01-01`; invalid settlement dates are blank. V1 resolves
tickers through instrument metadata and may use an external resolver. That
ticker-resolution behavior must not be reproduced in the V2 UI.

V1 computes a fallback key using the canonical identity inputs above, looks up
an existing row by broker ID first and fallback key second, then updates or
inserts. It performs no preview or confirmation: the caller invokes a direct
write import. V2 must preserve the useful parsing/identity semantics only
through a V2 engine service, not by copying V1's immediate-write workflow.

## Required UI-Safe Call Flow

Import:

1. Streamlit passes uploaded bytes to a V2 Nordnet parser/dry-run service.
2. The service returns source/valid/invalid/duplicate/would-insert/would-update
   counts, bounded normalized preview rows, and validation errors.
3. Streamlit renders that result and collects explicit confirmation only.
4. Streamlit invokes the corresponding engine-owned confirmed write service.
5. The service owns schema preconditions, canonical mapping, identity,
   deduplication, transaction writes, and an import receipt; the page reruns.

Settings:

1. Streamlit gathers controls into `HoldingSettings`.
2. Streamlit calls the explicit engine settings persistence boundary.
3. The engine writes through `save_holding_settings`; the page reloads the
   read-only page result.

## Smallest Next Implementation Slice

Add one V2-native Nordnet import service with parser, dry-run/preview result,
and explicit confirmed write operation. It must use the existing canonical
transaction, fallback-identity, schema-initialization, and upsert primitives;
it must not import V1 code, resolve tickers through external services, or put
any import semantics in Streamlit. Focused temporary-SQLite tests should cover
UTF-16/UTF-8 tab files, headers, normalization, malformed rows, overlapping
imports, both identity paths, dry-run no-write behavior, and explicit schema
preconditions.