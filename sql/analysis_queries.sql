-- Query 01 - Match list with competition, season, teams, and statistics.
-- Business purpose: provide a joined operator/export view of fixture context.
SELECT m.external_id, c.name AS competition, s.name AS season,
       home.name AS home_team, m.home_score, m.away_score, away.name AS away_team,
       m.status, ms.home_possession, ms.away_possession
FROM matches AS m
JOIN seasons AS s ON s.id = m.season_id
JOIN competitions AS c ON c.id = s.competition_id
JOIN teams AS home ON home.id = m.home_team_id
JOIN teams AS away ON away.id = m.away_team_id
LEFT JOIN match_statistics AS ms ON ms.match_id = m.id
ORDER BY m.kickoff_at DESC;

-- Query 02 - Match volume and goals by competition and season (GROUP BY).
-- Business purpose: compare competition scoring volume and season productivity.
SELECT c.name AS competition, s.name AS season, COUNT(*) AS matches,
       SUM(COALESCE(m.home_score, 0) + COALESCE(m.away_score, 0)) AS total_goals,
       ROUND(AVG(COALESCE(m.home_score, 0) + COALESCE(m.away_score, 0)), 2) AS avg_goals
FROM matches AS m
JOIN seasons AS s ON s.id = m.season_id
JOIN competitions AS c ON c.id = s.competition_id
GROUP BY c.name, s.name
ORDER BY c.name, s.name;

-- Query 03 - Competitions exceeding the average-goals threshold (HAVING).
-- Business purpose: find competitions whose scoring profile merits review.
SELECT c.name AS competition, COUNT(*) AS matches,
       AVG(m.home_score + m.away_score) AS avg_goals
FROM matches AS m
JOIN seasons AS s ON s.id = m.season_id
JOIN competitions AS c ON c.id = s.competition_id
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
GROUP BY c.name
HAVING AVG(m.home_score + m.away_score) > 2.0
ORDER BY avg_goals DESC;

-- Query 04 - Label each match result using CASE.
-- Business purpose: classify completed and pending fixtures for reporting.
SELECT m.external_id, home.name AS home_team, away.name AS away_team,
       m.home_score, m.away_score,
       CASE
           WHEN m.home_score IS NULL OR m.away_score IS NULL THEN 'PENDING'
           WHEN m.home_score > m.away_score THEN 'HOME_WIN'
           WHEN m.home_score < m.away_score THEN 'AWAY_WIN'
           ELSE 'DRAW'
       END AS result
FROM matches AS m
JOIN teams AS home ON home.id = m.home_team_id
JOIN teams AS away ON away.id = m.away_team_id
ORDER BY m.external_id;

-- Query 05 - Team performance across home and away fixtures (CTE and UNION ALL).
-- Business purpose: compare team points and goals across both venues.
WITH team_results AS (
    SELECT home_team_id AS team_id, home_score AS goals_for,
           away_score AS goals_against, 'HOME' AS venue
    FROM matches
    WHERE home_score IS NOT NULL AND away_score IS NOT NULL
    UNION ALL
    SELECT away_team_id AS team_id, away_score AS goals_for,
           home_score AS goals_against, 'AWAY' AS venue
    FROM matches
    WHERE home_score IS NOT NULL AND away_score IS NOT NULL
)
SELECT t.name AS team, COUNT(*) AS played, SUM(tr.goals_for) AS goals_for,
       SUM(tr.goals_against) AS goals_against,
       SUM(CASE WHEN tr.goals_for > tr.goals_against THEN 3
                WHEN tr.goals_for = tr.goals_against THEN 1 ELSE 0 END) AS points
FROM team_results AS tr
JOIN teams AS t ON t.id = tr.team_id
GROUP BY t.id, t.name
ORDER BY points DESC, goals_for DESC, t.name;

-- Query 06 - Matches with more goals than the database-wide average (subquery).
-- Business purpose: identify high-scoring fixtures relative to the demo baseline.
SELECT m.external_id, home.name AS home_team, away.name AS away_team,
       m.home_score, m.away_score
FROM matches AS m
JOIN teams AS home ON home.id = m.home_team_id
JOIN teams AS away ON away.id = m.away_team_id
WHERE m.home_score + m.away_score > (
    SELECT AVG(home_score + away_score)
    FROM matches
    WHERE home_score IS NOT NULL AND away_score IS NOT NULL
)
ORDER BY (m.home_score + m.away_score) DESC;

-- Query 07 - Rank teams by average goals per appearance (window function).
-- Business purpose: compare attacking output while accounting for appearances.
WITH team_appearances AS (
    SELECT home_team_id AS team_id, home_score AS goals
    FROM matches
    WHERE home_score IS NOT NULL
    UNION ALL
    SELECT away_team_id AS team_id, away_score AS goals
    FROM matches
    WHERE away_score IS NOT NULL
),
team_averages AS (
    SELECT team_id, AVG(goals) AS avg_goals
    FROM team_appearances
    GROUP BY team_id
)
SELECT t.name AS team, ROUND(ta.avg_goals, 2) AS avg_goals,
       RANK() OVER (ORDER BY ta.avg_goals DESC) AS goals_rank
FROM team_averages AS ta
JOIN teams AS t ON t.id = ta.team_id
ORDER BY goals_rank, t.name;

-- Query 08 - Event timeline with the prior event minute (LAG window function).
-- Business purpose: review event spacing and match chronology.
SELECT m.external_id, e.minute, e.event_type, e.description,
       LAG(e.minute) OVER (PARTITION BY e.match_id ORDER BY e.minute, e.id)
           AS previous_event_minute
FROM match_events AS e
JOIN matches AS m ON m.id = e.match_id
ORDER BY m.external_id, e.minute, e.id;

-- Query 09 - Count recorded events by competition and event type.
-- Business purpose: compare event coverage and event mix across competitions.
SELECT c.name AS competition, e.event_type, COUNT(*) AS event_count
FROM match_events AS e
JOIN matches AS m ON m.id = e.match_id
JOIN seasons AS s ON s.id = m.season_id
JOIN competitions AS c ON c.id = s.competition_id
GROUP BY c.name, e.event_type
ORDER BY c.name, event_count DESC, e.event_type;

-- Query 10 - Open data-quality alerts grouped by severity and rule.
-- Business purpose: prioritize unresolved validation workload.
SELECT severity, rule_code, COUNT(*) AS alert_count,
       MIN(detected_at) AS first_detected
FROM data_quality_alerts
WHERE resolved_at IS NULL
GROUP BY severity, rule_code
ORDER BY CASE severity WHEN 'ERROR' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END,
         alert_count DESC;

-- Query 11 - Matches missing statistics or any recorded events (LEFT JOIN).
-- Business purpose: identify incomplete match records before analysis.
SELECT m.external_id, home.name AS home_team, away.name AS away_team,
       CASE WHEN ms.id IS NULL THEN 1 ELSE 0 END AS missing_statistics,
       CASE WHEN COUNT(e.id) = 0 THEN 1 ELSE 0 END AS missing_events
FROM matches AS m
JOIN teams AS home ON home.id = m.home_team_id
JOIN teams AS away ON away.id = m.away_team_id
LEFT JOIN match_statistics AS ms ON ms.match_id = m.id
LEFT JOIN match_events AS e ON e.match_id = m.id
GROUP BY m.id, m.external_id, home.name, away.name, ms.id
HAVING ms.id IS NULL OR COUNT(e.id) = 0
ORDER BY m.external_id;

-- Query 12 - Find duplicate event fingerprints for quality-control review.
-- Business purpose: surface repeated event content missed by key checks.
SELECT m.external_id, e.minute, e.event_type, e.team_id, e.description,
       COUNT(*) AS duplicate_count
FROM match_events AS e
JOIN matches AS m ON m.id = e.match_id
GROUP BY m.external_id, e.minute, e.event_type, e.team_id, e.description
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, m.external_id, e.minute;

-- Query 13 - Rank the largest simulated odds movements within each market type.
-- Business purpose: prioritize unusual price changes for operator review.
WITH movements AS (
    SELECT m.external_id, mk.market_type, ms.selection_name,
           oh.previous_odds, oh.current_odds,
           ROUND(100.0 * (oh.current_odds - oh.previous_odds)
                 / oh.previous_odds, 2) AS change_percent,
           RANK() OVER (
               PARTITION BY mk.market_type
               ORDER BY ABS(oh.current_odds - oh.previous_odds)
                        / oh.previous_odds DESC
           ) AS movement_rank
    FROM odds_history AS oh
    JOIN market_selections AS ms ON ms.id = oh.selection_id
    JOIN markets AS mk ON mk.id = ms.market_id
    JOIN matches AS m ON m.id = mk.match_id
)
SELECT external_id, market_type, selection_name, previous_odds,
       current_odds, change_percent, movement_rank
FROM movements
WHERE movement_rank <= 5
ORDER BY market_type, movement_rank, external_id;

-- Query 14 - Aggregate price movement size by market type and source.
-- Business purpose: compare typical odds-update magnitudes by feed type.
SELECT mk.market_type, oh.source, COUNT(*) AS updates,
       ROUND(AVG(ABS(oh.current_odds - oh.previous_odds)), 3) AS avg_abs_odds_change,
       ROUND(AVG(ABS(100.0 * (oh.current_odds - oh.previous_odds)
                     / oh.previous_odds)), 2) AS avg_abs_percent_change
FROM odds_history AS oh
JOIN market_selections AS ms ON ms.id = oh.selection_id
JOIN markets AS mk ON mk.id = ms.market_id
GROUP BY mk.market_type, oh.source
HAVING COUNT(*) > 0
ORDER BY mk.market_type, oh.source;

-- Query 15 - Compare trading-risk alert volumes and acknowledgement rates.
-- Business purpose: monitor risk alert workload and operator follow-through.
SELECT mk.market_type, tra.severity, COUNT(*) AS alerts,
       SUM(CASE WHEN tra.acknowledged_at IS NULL THEN 1 ELSE 0 END) AS open_alerts,
       ROUND(100.0 * SUM(
           CASE WHEN tra.acknowledged_at IS NOT NULL THEN 1 ELSE 0 END
       ) / COUNT(*), 2) AS acknowledged_percent
FROM trading_risk_alerts AS tra
JOIN odds_history AS oh ON oh.id = tra.odds_history_id
JOIN market_selections AS ms ON ms.id = oh.selection_id
JOIN markets AS mk ON mk.id = ms.market_id
GROUP BY mk.market_type, tra.severity
ORDER BY mk.market_type, tra.severity;

-- Query 16 - Summarize open, suspended, and closed markets by competition.
-- Business purpose: review market availability by competition and market type.
SELECT c.name AS competition, mk.market_type, mk.status, COUNT(*) AS markets
FROM markets AS mk
JOIN matches AS m ON m.id = mk.match_id
JOIN seasons AS s ON s.id = m.season_id
JOIN competitions AS c ON c.id = s.competition_id
GROUP BY c.name, mk.market_type, mk.status
ORDER BY c.name, mk.market_type, mk.status;

-- Query 17 - Review trading audit activity by action and operator.
-- Business purpose: summarize recorded operator and simulated system actions.
SELECT event_type, actor, COUNT(*) AS actions,
       MIN(created_at) AS first_action,
       MAX(created_at) AS last_action,
       SUM(CASE WHEN is_simulated THEN 1 ELSE 0 END) AS simulated_actions
FROM trading_audit_log
GROUP BY event_type, actor
ORDER BY actions DESC, event_type, actor;

-- Query 18 - Historical scoring and result profile by competition and season.
-- Business purpose: compare outcome mix and scoring without simulated matches.
SELECT c.name AS competition, s.name AS season, COUNT(*) AS matches,
       SUM(CASE WHEN hm.home_score > hm.away_score THEN 1 ELSE 0 END) AS home_wins,
       SUM(CASE WHEN hm.home_score = hm.away_score THEN 1 ELSE 0 END) AS draws,
       SUM(CASE WHEN hm.home_score < hm.away_score THEN 1 ELSE 0 END) AS away_wins,
       ROUND(AVG(hm.home_score + hm.away_score), 2) AS avg_total_goals
FROM historical_matches AS hm
JOIN seasons AS s ON s.id = hm.season_id
JOIN competitions AS c ON c.id = s.competition_id
GROUP BY c.name, s.name
ORDER BY c.name, s.name;

-- Query 19 - Rank historical teams using home and away performances.
-- Business purpose: build a season standings view from match-level results.
WITH team_results AS (
    SELECT season_id, home_team_id AS team_id, home_score AS goals_for,
           away_score AS goals_against, kickoff_date
    FROM historical_matches
    UNION ALL
    SELECT season_id, away_team_id AS team_id, away_score AS goals_for,
           home_score AS goals_against, kickoff_date
    FROM historical_matches
),
team_table AS (
    SELECT season_id, team_id, COUNT(*) AS played,
           SUM(goals_for) AS goals_for, SUM(goals_against) AS goals_against,
           SUM(CASE WHEN goals_for > goals_against THEN 3
                    WHEN goals_for = goals_against THEN 1 ELSE 0 END) AS points
    FROM team_results
    GROUP BY season_id, team_id
)
SELECT c.name AS competition, s.name AS season, t.name AS team,
       tt.played, tt.points, tt.goals_for, tt.goals_against,
       RANK() OVER (
           PARTITION BY tt.season_id
           ORDER BY tt.points DESC, tt.goals_for - tt.goals_against DESC,
                    tt.goals_for DESC
       ) AS table_position
FROM team_table AS tt
JOIN seasons AS s ON s.id = tt.season_id
JOIN competitions AS c ON c.id = s.competition_id
JOIN teams AS t ON t.id = tt.team_id
ORDER BY c.name, s.name, table_position, t.name;

-- Query 20 - Five latest results and prior-result points for each team.
-- Business purpose: inspect recent form while preserving match chronology.
WITH appearances AS (
    SELECT season_id, home_team_id AS team_id, kickoff_date,
           home_score AS goals_for, away_score AS goals_against
    FROM historical_matches
    UNION ALL
    SELECT season_id, away_team_id AS team_id, kickoff_date,
           away_score AS goals_for, home_score AS goals_against
    FROM historical_matches
),
recent_form AS (
    SELECT season_id, team_id, kickoff_date, goals_for, goals_against,
           CASE WHEN goals_for > goals_against THEN 3
                WHEN goals_for = goals_against THEN 1 ELSE 0 END AS points,
           ROW_NUMBER() OVER (
               PARTITION BY season_id, team_id ORDER BY kickoff_date DESC
           ) AS recent_rank,
           LAG(CASE WHEN goals_for > goals_against THEN 3
                    WHEN goals_for = goals_against THEN 1 ELSE 0 END)
               OVER (PARTITION BY season_id, team_id ORDER BY kickoff_date DESC)
               AS prior_result_points
    FROM appearances
)
SELECT c.name AS competition, s.name AS season, t.name AS team,
       rf.kickoff_date, rf.goals_for, rf.goals_against, rf.points,
       rf.prior_result_points
FROM recent_form AS rf
JOIN seasons AS s ON s.id = rf.season_id
JOIN competitions AS c ON c.id = s.competition_id
JOIN teams AS t ON t.id = rf.team_id
WHERE rf.recent_rank <= 5
ORDER BY c.name, s.name, t.name, rf.recent_rank;

-- Query 21 - Audit import batch volume and row-quality outcomes.
-- Business purpose: verify source lineage and identify batches with rejected rows.
SELECT hib.source_name, hib.source_url, c.name AS competition, s.name AS season,
       hib.file_sha256, hib.rows_seen, hib.imported_count,
       hib.duplicate_count, hib.invalid_count, hib.imported_at
FROM historical_import_batches AS hib
JOIN competitions AS c ON c.id = hib.competition_id
JOIN seasons AS s ON s.id = hib.season_id
ORDER BY hib.imported_at DESC, hib.id DESC;

-- Query 22 - Detect historical data integrity and season-boundary issues.
-- Business purpose: independently validate row linkage and declared season dates.
SELECT hm.id, c.name AS competition, s.name AS season,
       hm.kickoff_date, home.name AS home_team, away.name AS away_team,
       CASE
           WHEN hm.home_team_id = hm.away_team_id THEN 'SAME_TEAM'
           WHEN hm.home_score < 0 OR hm.away_score < 0 THEN 'NEGATIVE_SCORE'
           WHEN hm.kickoff_date < s.start_date OR hm.kickoff_date > s.end_date
               THEN 'DATE_OUTSIDE_SEASON'
           WHEN hib.season_id <> hm.season_id THEN 'BATCH_SEASON_MISMATCH'
           ELSE 'VALID'
       END AS integrity_status
FROM historical_matches AS hm
JOIN seasons AS s ON s.id = hm.season_id
JOIN competitions AS c ON c.id = s.competition_id
JOIN teams AS home ON home.id = hm.home_team_id
JOIN teams AS away ON away.id = hm.away_team_id
JOIN historical_import_batches AS hib ON hib.id = hm.source_batch_id
WHERE hm.home_team_id = hm.away_team_id
   OR hm.home_score < 0
   OR hm.away_score < 0
   OR hm.kickoff_date < s.start_date
   OR hm.kickoff_date > s.end_date
   OR hib.season_id <> hm.season_id
ORDER BY hm.kickoff_date, hm.id;

-- Query 23 - Identify the origin and quantity of match-monitor records.
-- Business purpose: keep generated portfolio fixtures distinct from imports.
SELECT data_origin, source_name, COUNT(*) AS matches,
       MIN(kickoff_at) AS first_kickoff, MAX(kickoff_at) AS last_kickoff
FROM matches
GROUP BY data_origin, source_name
ORDER BY data_origin, source_name;
