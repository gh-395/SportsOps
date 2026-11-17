# Bundled historical results

SportsOps includes completed football results from
[OpenFootball's football.json repository](https://github.com/openfootball/football.json),
covering the English Premier League, German Bundesliga, Spanish La Liga, and
Italian Serie A from 2018/19 through 2023/24.

The repository README states under License that its schema, data, and scripts
are dedicated to the public domain and may be used without restrictions:
[README license statement](https://github.com/openfootball/football.json#license).
This notice records the source's stated terms; it is not legal advice.

The bundled CSVs contain dates, teams, and full-time scores only. Half-time
scores, events, possession, shots, and other statistics are not included in the
historical database. The result code is derived directly from each source
full-time score. Source URLs and source-file SHA-256 values are recorded in
`bundle_manifest.json`; each import batch records its source URL, reuse terms,
CSV SHA-256, and import counts in the database.

The historical tables are separate from synthetic monitor fixtures and
simulated trading markets. SportsOps never derives odds or trading data from
these historical results.
