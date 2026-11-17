# SportsOps - Technical Documentation

## PostgreSQL

Install PostgreSQL separately if you want to run the production-style database.
Install its Python driver into the project environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-postgres.txt
```

Create a PostgreSQL database, then execute [`sql/schema.sql`](../sql/schema.sql)
against that database (for example, with `psql` or pgAdmin). Keep the password
out of the URL and source files; `psycopg` reads it from the process-local
`PGPASSWORD` environment variable:

```powershell
$env:PGHOST = "127.0.0.1"
$env:PGPORT = "5432"
$env:PGUSER = "YOUR_DATABASE_USER"
$env:PGDATABASE = "sportsops"
$securePassword = Read-Host "PostgreSQL password" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    $securePassword.Dispose()
}
$env:SPORTSOPS_DATABASE_URL = "postgresql+psycopg://$($env:PGUSER)@$($env:PGHOST):$($env:PGPORT)/$($env:PGDATABASE)"
.\.venv\Scripts\python.exe -m sportsops
```

Use your own database username and password locally; do not send credentials
in chat or commit them. The environment settings above last only for the
current PowerShell process and its children. The app creates missing tables
through SQLAlchemy, seeds match records only into an empty database, and adds
missing demo markets without replacing existing rows or prices. The bundled
historical CSVs are imported automatically only for the SQLite demo. To use
them with PostgreSQL, import the permitted files through **Historical Data >
Import CSV...** or the CSV CLI. The `schema.sql` DDL uses `IF NOT EXISTS` for
tables and indexes; it does not alter existing columns. Database connection,
SQL, and constraint failures are allowed to surface; they are not replaced
with simulated success data.

`sql/schema.sql` is the hand-written PostgreSQL DDL for inspection and setup.
The ORM models in [`sportsops/database/models.py`](../sportsops/database/models.py)
are the application mapping for both engines. [`sql/analysis_queries.sql`](../sql/analysis_queries.sql)
contains standalone PostgreSQL-compatible analysis examples.

### Reproducing the PostgreSQL integration tests on Windows

The verifier requires a dedicated disposable database named `sportsops_test`.
It creates uniquely named temporary schemas inside that database, executes
`schema.sql`, compares table/column coverage with the ORM, runs the event
simulation and analysis queries, checks constraints/triggers and a
data-preserving migration, and removes only its own temporary schemas. It
refuses any database whose name is not exactly `sportsops_test`; it does not
create or drop the database.

First, verify PostgreSQL is running and accepting connections:

```powershell
Get-Service postgresql-x64-18
Test-NetConnection 127.0.0.1 -Port 5432
```

Create a dedicated database role and database using pgAdmin or `psql` as a
local PostgreSQL administrator. Before creating them, check that neither name
already exists; do not overwrite or repurpose an existing database. For
example, connect to the maintenance database with the administrator account,
then run:

```sql
CREATE ROLE sportsops_test LOGIN;
```

In the `psql` prompt, set that role's password interactively using
`\password sportsops_test` (the password is not part of a SQL statement). Then
create the separate database:

```sql
CREATE DATABASE sportsops_test OWNER sportsops_test;
```

Install the driver into the project virtual environment and configure
connection details in the current PowerShell process. The password prompt is
masked and the verifier URL contains no password:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-postgres.txt
$env:PGHOST = "127.0.0.1"
$env:PGPORT = "5432"
$env:PGUSER = "sportsops_test"
$env:PGDATABASE = "sportsops_test"
$securePassword = Read-Host "sportsops_test password" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    $securePassword.Dispose()
}
$env:SPORTSOPS_TEST_POSTGRES_URL = "postgresql+psycopg://$($env:PGUSER)@$($env:PGHOST):$($env:PGPORT)/$($env:PGDATABASE)"
.\.venv\Scripts\python.exe scripts\verify_postgres.py
.\.venv\Scripts\python.exe scripts\validate_sql.py --database-url $env:SPORTSOPS_TEST_POSTGRES_URL
Remove-Item Env:PGPASSWORD, Env:PGHOST, Env:PGPORT, Env:PGUSER, Env:PGDATABASE, Env:SPORTSOPS_TEST_POSTGRES_URL
```

Do not point this verifier at a production or shared database. If any command
fails, fix the local service, credentials, or role/database setup before
rerunning it. The SQLite user database and packaged app are not involved.

## Entity relationship diagram

```mermaid
erDiagram
    COMPETITIONS ||--o{ SEASONS : contains
    SEASONS ||--o{ MATCHES : schedules
    TEAMS ||--o{ MATCHES : "home team"
    TEAMS ||--o{ MATCHES : "away team"
    MATCHES ||--o{ MATCH_EVENTS : records
    TEAMS o|--o{ MATCH_EVENTS : attributes
    MATCHES ||--o| MATCH_STATISTICS : summarizes
    MATCHES o|--o{ DATA_QUALITY_ALERTS : flags
    MATCHES ||--o{ MARKETS : offers
    MARKETS ||--|{ MARKET_SELECTIONS : contains
    MARKET_SELECTIONS ||--o{ ODDS_HISTORY : changes
    ODDS_HISTORY ||--o| TRADING_RISK_ALERTS : may_trigger
    MARKETS o|--o{ TRADING_AUDIT_LOG : records
    MARKET_SELECTIONS o|--o{ TRADING_AUDIT_LOG : references
    SIMULATION_RUNS ||--o{ SIMULATION_MATCH_STATES : owns
    SIMULATION_RUNS ||--o{ SIMULATION_EVENTS : generates
    MATCHES ||--o{ SIMULATION_EVENTS : receives
    SIMULATION_RUNS ||--o{ SIMULATION_MARKET_STATES : scopes
    MARKETS ||--o{ SIMULATION_MARKET_STATES : status
    SIMULATION_RUNS ||--o{ SIMULATION_SELECTION_QUOTES : scopes
    MARKET_SELECTIONS ||--o{ SIMULATION_SELECTION_QUOTES : quotes
    SIMULATION_RUNS ||--o{ SIMULATION_ODDS_HISTORY : records
    SIMULATION_EVENTS o|--o{ SIMULATION_ODDS_HISTORY : explains
    SIMULATION_RUNS o|--o{ SIMULATION_AUDIT_LOG : audits
    MATCHES ||--o{ TRADING_RISK_INCIDENTS : groups
    TRADING_RISK_INCIDENTS ||--o{ TRADING_RISK_OCCURRENCES : contains

    COMPETITIONS {
        int id PK
        string name UK
        string country
    }
    SEASONS {
        int id PK
        int competition_id FK
        string name
        date start_date
        date end_date
    }
    TEAMS {
        int id PK
        string name
        string country
    }
    MATCHES {
        int id PK
        string external_id UK
        int season_id FK
        int home_team_id FK
        int away_team_id FK
        datetime kickoff_at
        string status
        int home_score
        int away_score
        boolean is_simulated
        string data_origin
        string source_name
    }
    MATCH_EVENTS {
        int id PK
        int match_id FK
        int team_id FK
        string event_key
        int minute
        string event_type
        string description
    }
    MATCH_STATISTICS {
        int id PK
        int match_id FK, UK
        decimal home_possession
        decimal away_possession
        int home_shots
        int away_shots
    }
    DATA_QUALITY_ALERTS {
        int id PK
        int match_id FK
        string rule_code
        string severity
        datetime detected_at
        datetime resolved_at
    }
    MARKETS {
        int id PK
        int match_id FK
        string market_type
        string status
        boolean is_simulated
    }
    MARKET_SELECTIONS {
        int id PK
        int market_id FK
        string selection_key
        decimal opening_odds
        decimal current_odds
    }
    ODDS_HISTORY {
        int id PK
        int selection_id FK
        decimal previous_odds
        decimal current_odds
        datetime changed_at
        string idempotency_key UK
    }
    TRADING_RISK_ALERTS {
        int id PK
        int odds_history_id FK, UK
        string severity
        datetime acknowledged_at
        string acknowledgement_note
    }
    TRADING_AUDIT_LOG {
        int id PK
        int market_id FK
        int selection_id FK
        string event_type
        datetime created_at
        boolean is_simulated
    }
    SIMULATION_RUNS {
        string run_id PK
        int seed
        int speed
        string status
        datetime started_at
    }
    SIMULATION_MATCH_STATES {
        int id PK
        string run_id FK
        int match_id FK
        int minute
        int home_score
        int away_score
    }
    SIMULATION_EVENTS {
        int id PK
        string run_id FK
        int match_id FK
        string event_key UK
        int event_sequence
        int minute
        string event_type
    }
    SIMULATION_MARKET_STATES {
        int id PK
        string run_id FK
        int market_id FK
        string status
        string suspension_kind
    }
    SIMULATION_SELECTION_QUOTES {
        string run_id PK, FK
        int selection_id PK, FK
        decimal decimal_odds
        decimal fair_probability
        string reason
    }
    SIMULATION_ODDS_HISTORY {
        int id PK
        string run_id FK
        int selection_id FK
        int event_id FK
        decimal previous_odds
        decimal current_odds
        string reason
    }
    SIMULATION_AUDIT_LOG {
        int id PK
        string run_id FK
        int match_id FK
        int market_id FK
        string event_type
        datetime created_at
    }
    TRADING_RISK_INCIDENTS {
        int id PK
        int match_id FK
        int market_id FK
        int selection_id FK
        string rule_code
        int occurrence_count
        string status
    }
    TRADING_RISK_OCCURRENCES {
        int id PK
        int incident_id FK
        int legacy_alert_id FK
        int simulation_odds_history_id FK
        datetime occurred_at
    }
    COMPETITIONS ||--o{ HISTORICAL_IMPORT_BATCHES : scopes
    SEASONS ||--o{ HISTORICAL_IMPORT_BATCHES : covers
    HISTORICAL_IMPORT_BATCHES ||--o{ HISTORICAL_MATCHES : imports
    SEASONS ||--o{ HISTORICAL_MATCHES : classifies
    TEAMS ||--o{ HISTORICAL_MATCHES : "home or away"
    HISTORICAL_IMPORT_BATCHES {
        int id PK
        int competition_id FK
        int season_id FK
        string source_name
        string source_url
        string reuse_terms
        string file_sha256 UK
        int imported_count
        int duplicate_count
        int invalid_count
    }
    HISTORICAL_MATCHES {
        int id PK
        string match_key UK
        int source_batch_id FK
        int season_id FK
        int home_team_id FK
        int away_team_id FK
        date kickoff_date
        time kickoff_time
        int home_score
        int away_score
    }
```

## Database design

- **`competitions` and `seasons`** separate competition identity from a
  competition's season. Season names are unique within a competition and dates
  are checked.
- **`teams`** stores reusable team identities, unique by name and country.
- **`matches`** references its season and distinct home/away teams. External
  IDs are unique; status, nonnegative scores, and elapsed minutes are checked.
  `updated_at` records the last persisted update time.
  `data_origin`, `source_name`, and optional `source_reference` label the
  record's provenance; origin and simulated-state constraints must agree.
- **`match_events`** stores event-level detail. `(match_id, event_key)` is
  unique, and match/minute is indexed for timeline queries.
- **`match_statistics`** stores one statistics row per match, with possession
  ranges and shot-count constraints.
- **`data_quality_alerts`** records rule, severity, message, detection time,
  optional acknowledgement/resolution times. Deleting a match preserves its
  alert while clearing the match reference.
- **`markets` and `market_selections`** normalize each match's 1X2,
  Over/Under 2.5, and Both Teams To Score markets. A match can have only one
  market of each type; selection keys are unique within a market. Opening and
  current decimal odds must both be greater than 1.
- **`odds_history`** links each simulated price change to one market
  selection, recording previous/current prices, timestamp, source, and a
  unique idempotency key. Absolute and percentage changes are derived from
  those stored values.
- **`trading_risk_alerts`** references the price movement that triggered it,
  and persists severity, acknowledgement timestamp, and optional operator
  note. It remains separate from `data_quality_alerts`.
- **`trading_audit_log`** stores market actions and simulated updates. SQLite
  triggers and the PostgreSQL DDL reject updates/deletes so audit entries are
  append-only.
- **Phase 6 simulation tables** keep every run separate from base match and
  market rows. `simulation_match_states` stores the run's score and clock;
  `simulation_events` is an ordered, uniquely keyed event ledger;
  `simulation_market_states` and `simulation_selection_quotes` hold
  run-scoped status and prices; `simulation_odds_history` stores explainable
  price movements; and `simulation_audit_log` records simulation operations.
  `simulation_market_controls` carries operator suspensions between runs.
- **`trading_risk_incidents` and `trading_risk_occurrences`** group repeated
  movements by match, market, selection, and rule. Occurrence rows are
  append-only and link either a legacy Phase 4 alert or one Phase 6 odds
  movement. Legacy alert rows remain unchanged; startup backfills their group
  records idempotently.
- **`historical_import_batches` and `historical_matches`** hold user-imported
  completed results separately from monitor matches and trading markets.
  Batch rows record source URL, the operator's reuse-terms note, file SHA-256,
  row counts, and import timestamp. Historical rows retain their source batch,
  CSV row number, match key, result, and optional kickoff time. They share
  normalized competition/season/team identities but do not feed markets or
  simulation.

On an existing database, startup creates the additive Phase 6 tables and
append-only ledger triggers without dropping or rewriting existing user data.
It preserves legacy markets, odds history, risk alerts, and audit entries;
historical tables are not modified by trading simulation.
It also continues to migrate `matches.updated_at` and
`data_quality_alerts.acknowledged_at` for older databases. Missing demo
markets are inserted idempotently for simulated matches; existing markets and
prices are not overwritten.

The model uses integer surrogate primary keys, foreign keys, `DATE`,
timezone-aware timestamps, `BOOLEAN`, `NUMERIC`, and bounded text types. The
schema is normalized around competitions, seasons, teams, matches, events,
statistics, and alerts. SQLite enables foreign-key enforcement on every
connection.

The base seed contains seven synthetic demo matches, three competitions, three
seasons, fourteen teams, match statistics, and 15 simulated events. Match
records also have an explicit `data_origin`; the desktop identifies the demo
rows as synthetic. Each eligible match receives three markets and seven
selections, each linked by foreign key. Phase 6 does not edit these original
seed rows; its run state, simulation events, and quotes are stored separately.

## Data validation

Seed/input validation checks for duplicate events, negative or partial scores,
missing match fields or events, invalid minutes, impossible shot totals,
possession totals that do not add to approximately 100%, same-team fixtures,
invalid statuses, and events attributed to non-participant teams. Database
validation also scans for duplicate event fingerprints, invalid/missing
scores, missing statistics, inconsistent statistics, and event/team
mismatches. Findings are stored in `data_quality_alerts`; new findings are
logged and repeated unresolved alerts are not duplicated.

Database constraints reject invalid persisted values where practical. The
validation layer provides additional diagnostics for cross-row inconsistencies
that a simple row constraint cannot express.

## Historical football CSV imports and provenance

The bundled source is [OpenFootball football.json](https://github.com/openfootball/football.json).
Its repository README states that its schema, data, and scripts are dedicated
to the public domain and may be used without restrictions. The source and terms
are recorded in [`OPENFOOTBALL_NOTICE.md`](../sportsops/data/historical/OPENFOOTBALL_NOTICE.md)
and [`bundle_manifest.json`](../sportsops/data/historical/bundle_manifest.json).
No third-party data was invented. The bundle contains the 8,676 completed
full-time results reported in the manifest; the SQLite demo imports them
idempotently on first launch. Existing historical rows are retained and are
never overwritten by the bundle importer.

The generic importer accepts (`Date`, `HomeTeam`, `AwayTeam`, `FTHG`, `FTAG`,
and optional `FTR` and `Time`), checks season bounds, score/result consistency,
team names, and duplicate fixtures, and stores valid rows with source-batch
provenance. Rows with validation issues are reported and skipped while valid
rows in the same file can still be imported. Importing never adds rows to
`matches`, generates markets, or changes trading simulation data. Exact file
repeats are detected by SHA-256; fixture repeats by normalized competition,
season, date, and home/away names.

Before importing another provider's file, review current reuse terms and
confirm they allow your intended use. The importer records the source and the
operator's reuse-terms note; **it does not verify a license or grant
permission**. For a permitted dataset, save the CSV locally and use the desktop
**Import CSV...** action or the command line (edit the values and dates):

```powershell
.\.venv\Scripts\python.exe -m sportsops.data.import_historical "C:\Data\results.csv" `
  --competition "Example League" `
  --country "Example Country" `
  --season "2025/26" `
  --season-start 2025-08-01 `
  --season-end 2026-06-30 `
  --source-name "Dataset provider" `
  --source-url "https://provider.example/results.csv" `
  --reuse-terms "Terms reviewed YYYY-MM-DD; intended use permitted"
```

The command uses the configured SportsOps database (the same
`SPORTSOPS_DATABASE_URL` as the app, or the default per-user SQLite database).
It prints imported, duplicate, and invalid-row counts; invalid rows include
source CSV row numbers. The bundle importer runs automatically only for the
SQLite demo. PostgreSQL users can use the desktop CSV importer or the CLI to
load any source they are permitted to use.

## SQL analysis examples

The complete set is in [`sql/analysis_queries.sql`](../sql/analysis_queries.sql).
It includes 23 runnable, business-commented queries, including match analysis,
odds movements, risk-alert acknowledgement rates, market status, audit
activity, historical results, import lineage, and data-quality checks, such as:

```sql
SELECT c.name AS competition, COUNT(*) AS matches,
       AVG(m.home_score + m.away_score) AS avg_goals
FROM matches AS m
JOIN seasons AS s ON s.id = m.season_id
JOIN competitions AS c ON c.id = s.competition_id
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
GROUP BY c.name
HAVING AVG(m.home_score + m.away_score) > 2.0;
```

The examples demonstrate joins, aggregation, `HAVING`, `CASE`, common table
expressions, subqueries, `UNION ALL`, window functions (`RANK`, `LAG`, and
running/team analyses), and data-quality checks. They use SQL supported by
PostgreSQL and SQLite.

Run all 23 statements against an isolated in-memory SQLite database with
clearly labeled synthetic demo rows:

```powershell
.\.venv\Scripts\python.exe scripts\validate_sql.py
```

The script reports each query's result count and prints sample aggregate and
data-origin results. It intentionally does not import the separate licensed
historical bundle, so historical query 18 returns no rows in this synthetic-only
validation fixture. The app's SQLite database contains the 8,676 bundled
historical results. To validate a database you already initialized, pass its
URL; the script runs the queries in a read-only transaction and does not create
its schema:

```powershell
.\.venv\Scripts\python.exe scripts\validate_sql.py `
  --database-url "sqlite:///C:/Users/you/AppData/Local/SportsOps/sportsops_demo.sqlite3"
```

### Sample output (isolated synthetic demo, not historical results)

Generated by `scripts/validate_sql.py`; the kickoff timestamps are the fixed
values in the synthetic demo fixtures:

```text
Query 02:
La Liga | 2025/26 | 2 | 4 | 2.0
Premier League | 2025/26 | 3 | 3 | 1.0
Serie A | 2025/26 | 2 | 4 | 2.0
Query 23:
SYNTHETIC | SportsOps synthetic demo | 7 | 2026-11-13 19:45:00.000000 | 2026-11-15 16:30:00.000000
Query 18: no historical rows imported in the demo database.
```

### Historical data check

The imported results were verified through the SQLAlchemy repository and the
same query path used by the workspace:

```text
Dataset rows: 8,676 across 4 competitions and 6 seasons
English Premier League | 2022-23 | 380 matches | 20 teams
Manchester City FC | Played 38 | Points 89 | Goals 94-33 | Avg GF 2.47
```

## Architecture

- [`sportsops/database/models.py`](../sportsops/database/models.py): SQLAlchemy
  relational models and constraints.
- [`sportsops/database/engine.py`](../sportsops/database/engine.py): database
  URL configuration, SQLite foreign-key setup, schema initialization, and
  first-run seed orchestration.
- [`sportsops/database/seed.py`](../sportsops/database/seed.py): idempotent
  normalized demo-data insertion.
- [`sportsops/database/repository.py`](../sportsops/database/repository.py):
  query and map persisted matches, events, and alerts for the Qt model/view UI.
- [`sportsops/database/service.py`](../sportsops/database/service.py): scoped
  database sessions, market operations, simulated price changes, and persisted
  alert acknowledgement.
- [`sportsops/database/trading_repository.py`](../sportsops/database/trading_repository.py):
  market, odds-history, risk-alert, and audit queries and transactional
  operations.
- [`sportsops/database/migrations.py`](../sportsops/database/migrations.py):
  safe additive migrations for older SQLite/PostgreSQL columns and the
  append-only trading and simulation-ledger triggers.
- [`sportsops/database/historical_repository.py`](../sportsops/database/historical_repository.py):
  CSV validation, provenance, SHA-256 repeat detection, fixture deduplication,
  and historical-result reads.
- [`sportsops/data/import_historical.py`](../sportsops/data/import_historical.py):
  local-file import CLI; it never downloads a dataset.
- [`sportsops/data/historical_bundle.py`](../sportsops/data/historical_bundle.py):
  SQLite-only, manifest-validated import of the bundled source CSV files.
- [`sportsops/ui/historical_workspace.py`](../sportsops/ui/historical_workspace.py):
  SQL-filtered, paginated results table, match details, team/league
  aggregation, and import interface.
- [`scripts/validate_sql.py`](../scripts/validate_sql.py) and
  [`scripts/verify_postgres.py`](../scripts/verify_postgres.py): repeatable
  PostgreSQL-compatible query validation and optional isolated-schema
  PostgreSQL integration checks.
- [`sportsops/database/validation.py`](../sportsops/database/validation.py):
  input/database checks and alert persistence.
- [`sportsops/services/simulation.py`](../sportsops/services/simulation.py):
  legacy simulated-match helper retained for existing Phase 1Ã¢â‚¬â€œ3 callers.
- [`sportsops/services/football_simulation.py`](../sportsops/services/football_simulation.py):
  reproducible event schedules and the Poisson-based probability/odds model.
- [`sportsops/database/simulation_repository.py`](../sportsops/database/simulation_repository.py):
  run-scoped match state, event, odds, market-status, risk-grouping, and audit
  persistence.
- [`sportsops/services/market_simulation.py`](../sportsops/services/market_simulation.py):
  UI controller for start/pause/reset and speed control of event-driven runs.
- [`sportsops/data/seed_data.py`](../sportsops/data/seed_data.py): explicitly
  simulated seed fixtures, separate from the desktop UI.
- [`sportsops/ui/`](../sportsops/ui): native PySide6 interface with only the
  Markets and Historical Data destinations, plus Qt model/view tables,
  resizable panels, trading-risk/audit workflows, and import/provenance views.

The application loads database records into its Qt table model on startup.
The data-access code is kept separate from widgets so later changes can add
database workflows without turning the app into a web or dashboard
application.

See [`ARCHITECTURE.md`](../ARCHITECTURE.md) for the application boundaries,
data flows, migration approach, and Phase 5A limitations.

## Database verification and PostgreSQL integration

SQLite is the default database for the desktop demo and automated unit tests.

PostgreSQL 18 integration was successfully verified on a local Windows installation using the dedicated `sportsops_test` database. All nine integration checks passed, covering schema creation, ORM compatibility, simulation persistence, timezone handling, numeric constraints, SQL analysis queries, legacy migrations, and append-only audit protection.

The automated Python test suite passed 59 tests, and all 23 SQL analysis queries passed validation using the isolated SQLite demo database.

The PostgreSQL integration checks require a locally configured PostgreSQL server and are not executed as part of the default unit test suite.

## Tests

Run the automated tests from the project folder:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use isolated in-memory SQLite databases; no PostgreSQL server or live
data is needed. They verify seed contents and repeatability, constraints,
validation and alert acknowledgement persistence, market odds and filtering,
risk thresholds and notes, market transitions, append-only auditing,
deterministic event generation, second-half transitions, score/event
consistency, simulation reset, risk grouping, UI navigation, and execution of
all 23 analysis queries. Historical import tests use test-created CSVs, not a
third-party or purportedly real dataset.


