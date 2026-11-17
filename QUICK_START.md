# SportsOps quick start

1. Extract the complete `SportsOps` folder to a location you can write to.
   Keep `SportsOps.exe` together with the `_internal` folder.
2. Double-click `SportsOps.exe`.
3. Use the left navigation to switch between **Markets Workspace** and
   **Historical Data**. Markets Workspace opens by default. First launch
   initializes clearly labeled simulated markets and imports 8,676
   source-derived historical match results automatically.
4. The app works offline. It does not require Python, PostgreSQL, live sports
   feeds, bookmaker accounts, or betting services.

## Your local data

The default SQLite database and profile information are stored in:

```text
%LOCALAPPDATA%\SportsOps\
```

The initial database is `sportsops_demo.sqlite3`. Updates to the application
keep using your user-data database. Historical results are stored in separate
tables from the synthetic match records and simulated markets. Their source,
reuse terms, and import counts are available in Historical Data.

The bundled results cover four European leagues and six seasons (2018/19 to
2023/24). They include dates, teams, and full-time scores only; detailed match
statistics are not available and are not invented. The application does not
need internet access to start.

To start a clean synthetic demo without removing any existing data, choose
**File > Create fresh demo profile...**. The app creates a new database file,
leaves the current database untouched, and closes. Start `SportsOps.exe` again
to use the fresh demo. Historical or operator data in the previous database
remains in that original file and is not copied into the new demo profile.

## Windows security warning

An unsigned build can show a SmartScreen warning. Do not bypass warnings or
disable Windows security features. Only run a copy you trust and can verify
through its trusted source and checksum.
