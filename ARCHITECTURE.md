# SportsOps technical architecture

## Application boundaries

SportsOps is a Windows-native PySide6 desktop application. The main window has
exactly two navigation destinations: **Markets Workspace** and **Historical
Data**. It uses Qt widgets, model/view tables, and resizable splitters; it does
not embed a web server, browser UI, or HTML dashboard. Workspaces call the
database service instead of issuing SQL from widgets.

```text
Markets and Historical Data workspaces
                 |
         DatabaseService
                 |
      SQLAlchemy repositories
                 |
      SQLite or PostgreSQL
```

`sportsops/ui/` contains the two workspaces and their table models.
`sportsops/services/football_simulation.py` owns deterministic event schedules
and probability/odds calculations; the database simulation repository persists
each event, quote, incident, and audit action.
The former match-monitor UI is not a primary destination, but its normalized
tables, match records, repositories, and trading foreign-key relationships
remain intact.

## Data domains and provenance

- **Synthetic demo monitor data** remains in `matches`, `match_events`,
  `match_statistics`, and `data_quality_alerts`. Match records identify origin
  and source. Simulated markets and odds remain foreign-key-linked to these
  match records.
- **Historical football results** are stored in the separate
  `historical_import_batches` and `historical_matches` tables. A batch records
  the source name and URL, reuse-terms note, file SHA-256, import counts, and
  timestamp. Historical rows refer to batch, season, and teams; they have no
  path to markets or odds.
- **Trading operations** keep legacy odds history and risk alerts, plus
  run-scoped event-simulation history. Simulation match clocks, scores, market
  statuses, and quotes are overlays; original match scores, market statuses,
  opening prices, and historical results are not updated by a run.

## Event-driven football simulation

The UI timer fires once per second. At 1x, 5x, or 10x, each tick advances every
active simulated match by that number of virtual match minutes. A run stores
its integer seed, speed, and status. Seed plus match ID generates fixed home
and away expected-goal rates and an ordered event schedule; repeating the same
seed against the same seeded fixtures reproduces that schedule. Scheduled
events include kick-off, goals, red cards, half-time, second-half kick-off,
and full time. Event keys and per-match sequence numbers are unique.

The live model estimates remaining goals with independent Poisson
distributions. For each side, expected remaining goals are its deterministic
match rate multiplied by `(90 - minute) / 90`; a red card applies a 0.62
multiplier to that side and 1.35 to the opponent. The joint score distribution
produces the 1X2, Over/Under 2.5, and Both Teams To Score probabilities.
Probabilities are fair (sum to one within each market); quoted decimal odds
are calculated as `1 / (fair_probability * 1.05)`, rounded to three decimal
places, and bounded to 1.010–1000.000. The 1000.000 maximum applies to every
outcome, including positive but very small probabilities (the nine-decimal
probability representation can also round the smallest probabilities to zero).
At the maximum price the displayed implied probability is 0.10%. Both price
limits can make the displayed overround differ from exactly 5%. This bound
keeps quotes within the existing `NUMERIC(8, 3)` storage range. Given the
minimum valid prior stored price of 1.001, the largest possible percentage
change to 1000.000 is 99,800.0999%, within `NUMERIC(9, 4)`. The model is
deterministic and explainable, but it is not calibrated against real bookmaker
prices.

Quotes refresh at run start, major match events, after event suspensions, and
every five virtual minutes. Goal, red-card, and half-time events temporarily
suspend non-manually-suspended markets. They are repriced and reopened on a
later tick. Manual suspensions persist across runs until an operator reopens
them; reopening reprices first. Full time closes markets in that run, and the
repository excludes closed markets from repricing.

`simulation_events`, `simulation_odds_history`, `simulation_audit_log`, and
`trading_risk_occurrences` are append-only ledgers. A reset marks a run
`RESET`; it does not delete its events, quotes, movement history, risk
occurrences, or audits. Legacy Phase 4 risk alerts are retained and grouped
idempotently by match/market/selection. Phase 6 movements create grouped
incidents scoped to their simulation run while preserving every occurrence,
count, severity, and timestamp. Expected model repricing causes remain fully
audited but do not become risk incidents based only on movement size; the
configured warning and critical thresholds apply to unexpected causes.
Invalid generated odds or probabilities stop repricing explicitly. Since the
application has no external quote feed, external feed staleness is not a
meaningful alert type. Run filtering keeps the current/most-recent run useful
by default and makes prior or unscoped legacy incidents available through
historical views without reassigning them. Acknowledgements and a 60-second
cooldown reduce repeated incident noise. SQLite's timezone-naive datetimes are
interpreted as UTC and all displayed timestamps are converted to local time
with a date and numeric UTC offset; writes keep Last Seen at or after First
Seen.

Historical results remain independent: the event simulation only selects
synthetic demo matches with simulated markets, and its own state is stored in
the run tables. The demo match and historical tables are not advanced or
rewritten.

SQLite is the default and stores mutable files in the per-user Windows data
directory. Set `SPORTSOPS_DATABASE_URL` to select an explicitly configured
SQLite or PostgreSQL URL. SQLAlchemy creates missing tables; additive startup
migrations preserve older rows. PostgreSQL `sql/schema.sql` is separately
maintained DDL, not a substitute for migrations on existing databases.

## Bundled historical dataset

The SQLite demo imports its bundled CSVs at application startup through
`sportsops/data/historical_bundle.py`. The loader validates the manifest,
expected match counts, file paths, and importer results. Each file goes through
the normal CSV validation and deduplication path. Repeated launches are
idempotent; existing historical rows are never deleted or updated. Generic
database initialization and PostgreSQL startup do not import the bundle.

The bundle has **8,676 completed results** from 24 files: the English Premier
League, German Bundesliga, Spanish La Liga, and Italian Serie A across seasons
2018/19–2023/24. The source is
[OpenFootball football.json](https://github.com/openfootball/football.json).
Its README states that its schema, data, and scripts are dedicated to the
public domain and may be used without restrictions. The source URLs, reuse
terms, and source-file hashes are recorded in
`sportsops/data/historical/bundle_manifest.json`; the notice is in
`sportsops/data/historical/OPENFOOTBALL_NOTICE.md`.

The bundle contains dates, team names, and full-time scores only. It has no
half-time scores, events, possession, shots, or other detailed statistics.
SportsOps displays these values as unavailable and does not infer them.
Historical results remain isolated from simulated trading data.

## Historical exploration and imports

The Historical Data workspace uses SQL-side filters, sorting, counts, and
pagination rather than loading all matches into a Qt model. Filters include
competition, season, team, date range, result, and text search. Selecting a
match shows its result and import provenance. SQL aggregation derives
home/away records, goal averages, and league standings from completed scores.

An operator can import another local CSV after recording competition, country,
season bounds, source URL, and reuse terms. The importer validates encoding,
headers, dates, scores, result consistency, and season bounds. It reports
invalid rows, fingerprints files with SHA-256, and deduplicates normalized
competition/season/date/home/away fixtures. It records the operator's reuse
note but cannot verify licenses or grant permission. Same-team,
same-competition doubleheaders on one date would collide under the current
fixture key; this tradeoff should be revisited for a source where such fixtures
exist.

## SQL and verification

`sql/analysis_queries.sql` contains 23 PostgreSQL-compatible examples for
match and market analysis, historical results, import lineage, and
data-quality review. `scripts/validate_sql.py` runs the read-only queries
against an isolated synthetic SQLite demo or a caller-supplied initialized
database. The optional `scripts/verify_postgres.py` integration test uses
generated temporary schemas for PostgreSQL DDL, ORM initialization, query
execution, constraints, audit triggers, and a repeatable legacy migration; it
drops only those generated schemas.

The automated SQLite suite covers database constraints, old-schema data
preservation, importer idempotency, bundle totals and standings, workspace
navigation, historical filters, legacy alert grouping, deterministic event
generation, event/score consistency, odds calculations, market suspension and
closure, risk grouping, and append-only ledgers. PostgreSQL execution must be
performed separately using `scripts/verify_postgres.py` and a disposable
server URL; it is not claimed unless that script was actually run against a
PostgreSQL server. The simulated odds are not connected to a bookmaker or
live feed.
