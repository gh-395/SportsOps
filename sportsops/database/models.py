from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    country: Mapped[str] = mapped_column(String(100), nullable=False)


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("competition_id", "name", name="uq_seasons_competition_name"),
        CheckConstraint("start_date <= end_date", name="ck_seasons_dates"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competitions.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("name", "country", name="uq_teams_name_country"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(100), nullable=False)


class MatchRecord(Base):
    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('SCHEDULED', 'LIVE', 'HALF_TIME', 'FINISHED')",
            name="ck_matches_status",
        ),
        CheckConstraint(
            "(home_score IS NULL OR home_score >= 0) AND "
            "(away_score IS NULL OR away_score >= 0)",
            name="ck_matches_nonnegative_scores",
        ),
        CheckConstraint(
            "home_team_id <> away_team_id",
            name="ck_matches_distinct_teams",
        ),
        CheckConstraint(
            "elapsed_minute IS NULL OR elapsed_minute BETWEEN 0 AND 150",
            name="ck_matches_elapsed_minute",
        ),
        CheckConstraint(
            "data_origin IN ('SIMULATED', 'SYNTHETIC', 'HISTORICAL')",
            name="ck_matches_data_origin",
        ),
        CheckConstraint(
            "(data_origin = 'HISTORICAL' AND is_simulated = FALSE) OR "
            "(data_origin IN ('SIMULATED', 'SYNTHETIC') AND is_simulated = TRUE)",
            name="ck_matches_data_origin_simulated",
        ),
        Index("ix_matches_season_status", "season_id", "status"),
        Index("ix_matches_home_team", "home_team_id"),
        Index("ix_matches_away_team", "away_team_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="RESTRICT"), nullable=False
    )
    home_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False
    )
    away_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False
    )
    kickoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.current_timestamp(),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )
    elapsed_minute: Mapped[int | None] = mapped_column(SmallInteger)
    home_score: Mapped[int | None] = mapped_column(SmallInteger)
    away_score: Mapped[int | None] = mapped_column(SmallInteger)
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    data_origin: Mapped[str] = mapped_column(
        String(12), nullable=False, default="SYNTHETIC", server_default="'SYNTHETIC'"
    )
    source_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="SportsOps demo seed",
        server_default="'SportsOps demo seed'",
    )
    source_reference: Mapped[str | None] = mapped_column(String(255))


class MatchEvent(Base):
    __tablename__ = "match_events"
    __table_args__ = (
        UniqueConstraint("match_id", "event_key", name="uq_match_events_match_key"),
        CheckConstraint("minute BETWEEN 0 AND 150", name="ck_match_events_minute"),
        Index("ix_match_events_match_minute", "match_id", "minute"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    event_key: Mapped[str] = mapped_column(String(80), nullable=False)
    minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class MatchStatistics(Base):
    __tablename__ = "match_statistics"
    __table_args__ = (
        UniqueConstraint("match_id", name="uq_match_statistics_match"),
        CheckConstraint(
            "home_possession BETWEEN 0 AND 100 AND "
            "away_possession BETWEEN 0 AND 100",
            name="ck_match_statistics_possession_range",
        ),
        CheckConstraint(
            "home_shots >= 0 AND away_shots >= 0 AND "
            "home_shots_on_target >= 0 AND away_shots_on_target >= 0",
            name="ck_match_statistics_nonnegative_shots",
        ),
        CheckConstraint(
            "home_shots_on_target <= home_shots AND "
            "away_shots_on_target <= away_shots",
            name="ck_match_statistics_shots_on_target",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    home_possession: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    away_possession: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    home_shots: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    away_shots: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    home_shots_on_target: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    away_shots_on_target: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class DataQualityAlert(Base):
    __tablename__ = "data_quality_alerts"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'ERROR')",
            name="ck_data_quality_alerts_severity",
        ),
        Index("ix_data_quality_alerts_open", "resolved_at", "severity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int | None] = mapped_column(
        ForeignKey("matches.id", ondelete="SET NULL")
    )
    rule_code: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (
        UniqueConstraint("match_id", "market_type", name="uq_markets_match_type"),
        CheckConstraint(
            "market_type IN ('1X2', 'OVER_UNDER_2_5', 'BOTH_TEAMS_TO_SCORE')",
            name="ck_markets_type",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'SUSPENDED', 'CLOSED')",
            name="ck_markets_status",
        ),
        Index("ix_markets_status_type", "status", "market_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="RESTRICT"), nullable=False
    )
    market_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="OPEN", server_default="'OPEN'"
    )
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.current_timestamp(),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


class MarketSelection(Base):
    __tablename__ = "market_selections"
    __table_args__ = (
        UniqueConstraint(
            "market_id", "selection_key", name="uq_market_selections_market_key"
        ),
        CheckConstraint("opening_odds > 1", name="ck_market_selections_opening_odds"),
        CheckConstraint("current_odds > 1", name="ck_market_selections_current_odds"),
        Index("ix_market_selections_market", "market_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT"), nullable=False
    )
    selection_key: Mapped[str] = mapped_column(String(20), nullable=False)
    selection_name: Mapped[str] = mapped_column(String(100), nullable=False)
    opening_odds: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    current_odds: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)


class OddsHistory(Base):
    __tablename__ = "odds_history"
    __table_args__ = (
        CheckConstraint(
            "previous_odds > 1 AND current_odds > 1",
            name="ck_odds_history_valid_odds",
        ),
        CheckConstraint(
            "source IN ('SIMULATION', 'RESET')",
            name="ck_odds_history_source",
        ),
        UniqueConstraint("idempotency_key", name="uq_odds_history_idempotency"),
        Index("ix_odds_history_selection_time", "selection_id", "changed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    selection_id: Mapped[int] = mapped_column(
        ForeignKey("market_selections.id", ondelete="RESTRICT"), nullable=False
    )
    previous_odds: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    current_odds: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)


class TradingRiskAlert(Base):
    __tablename__ = "trading_risk_alerts"
    __table_args__ = (
        UniqueConstraint("odds_history_id", name="uq_trading_risk_alerts_history"),
        CheckConstraint(
            "severity IN ('WARNING', 'ERROR')",
            name="ck_trading_risk_alerts_severity",
        ),
        Index("ix_trading_risk_alerts_ack_severity", "acknowledged_at", "severity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    odds_history_id: Mapped[int] = mapped_column(
        ForeignKey("odds_history.id", ondelete="RESTRICT"), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledgement_note: Mapped[str | None] = mapped_column(Text)
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class TradingAuditLog(Base):
    __tablename__ = "trading_audit_log"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('MARKET_CREATED', 'ODDS_UPDATED', 'ODDS_RESET', "
            "'MARKET_SUSPENDED', 'MARKET_REOPENED', 'RISK_ALERT_ACKNOWLEDGED', "
            "'SIMULATION_STARTED', 'SIMULATION_PAUSED', "
            "'RISK_THRESHOLDS_CHANGED')",
            name="ck_trading_audit_event_type",
        ),
        UniqueConstraint("idempotency_key", name="uq_trading_audit_idempotency"),
        Index("ix_trading_audit_market_time", "market_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int | None] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT")
    )
    selection_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_selections.id", ondelete="RESTRICT")
    )
    odds_history_id: Mapped[int | None] = mapped_column(
        ForeignKey("odds_history.id", ondelete="RESTRICT")
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Operator", server_default="'Operator'"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(120))


class HistoricalImportBatch(Base):
    __tablename__ = "historical_import_batches"
    __table_args__ = (
        UniqueConstraint("file_sha256", name="uq_historical_import_file_sha256"),
        CheckConstraint("rows_seen >= 0", name="ck_historical_import_rows_seen"),
        CheckConstraint(
            "imported_count >= 0 AND duplicate_count >= 0 AND invalid_count >= 0",
            name="ck_historical_import_counts",
        ),
        Index("ix_historical_import_season", "season_id", "imported_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competitions.id", ondelete="RESTRICT"), nullable=False
    )
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="RESTRICT"), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    reuse_terms: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rows_seen: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    invalid_count: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class HistoricalMatch(Base):
    __tablename__ = "historical_matches"
    __table_args__ = (
        UniqueConstraint("match_key", name="uq_historical_matches_match_key"),
        CheckConstraint(
            "home_score >= 0 AND away_score >= 0",
            name="ck_historical_matches_nonnegative_scores",
        ),
        CheckConstraint(
            "home_team_id <> away_team_id",
            name="ck_historical_matches_distinct_teams",
        ),
        Index(
            "ix_historical_matches_season_date",
            "season_id",
            "kickoff_date",
        ),
        Index("ix_historical_matches_home_team", "home_team_id"),
        Index("ix_historical_matches_away_team", "away_team_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_batch_id: Mapped[int] = mapped_column(
        ForeignKey("historical_import_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="RESTRICT"), nullable=False
    )
    home_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False
    )
    away_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False
    )
    kickoff_date: Mapped[date] = mapped_column(Date, nullable=False)
    kickoff_time: Mapped[time | None] = mapped_column(Time)
    home_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    away_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class SimulationRun(Base):
    __tablename__ = "simulation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'PAUSED', 'COMPLETED', 'RESET')",
            name="ck_simulation_runs_status",
        ),
        CheckConstraint("speed IN (1, 5, 10)", name="ck_simulation_runs_speed"),
        CheckConstraint(
            "warning_percent > 0 AND critical_percent >= warning_percent",
            name="ck_simulation_runs_risk_thresholds",
        ),
        Index("ix_simulation_runs_created", "started_at"),
    )

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    speed: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="RUNNING", server_default="'RUNNING'"
    )
    warning_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("10")
    )
    critical_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("15")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SimulationMatchState(Base):
    __tablename__ = "simulation_match_states"
    __table_args__ = (
        UniqueConstraint("run_id", "match_id", name="uq_simulation_match_state"),
        CheckConstraint(
            "status IN ('SCHEDULED', 'LIVE', 'HALF_TIME', 'FINISHED')",
            name="ck_simulation_match_state_status",
        ),
        CheckConstraint("minute BETWEEN 0 AND 90", name="ck_simulation_match_minute"),
        CheckConstraint(
            "home_score >= 0 AND away_score >= 0 "
            "AND home_red_cards >= 0 AND away_red_cards >= 0",
            name="ck_simulation_match_nonnegative",
        ),
        CheckConstraint(
            "home_goal_rate > 0 AND away_goal_rate > 0",
            name="ck_simulation_match_goal_rates",
        ),
        Index("ix_simulation_match_states_run_status", "run_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    home_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    away_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    home_red_cards: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    away_red_cards: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    home_goal_rate: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False)
    away_goal_rate: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False)
    event_schedule: Mapped[str] = mapped_column(Text, nullable=False)
    next_event_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )


class SimulationEvent(Base):
    __tablename__ = "simulation_events"
    __table_args__ = (
        UniqueConstraint("run_id", "match_id", "event_key", name="uq_simulation_event_key"),
        UniqueConstraint(
            "run_id",
            "match_id",
            "event_sequence",
            name="uq_simulation_event_sequence",
        ),
        CheckConstraint("minute BETWEEN 0 AND 90", name="ck_simulation_event_minute"),
        Index(
            "ix_simulation_events_match_sequence",
            "run_id",
            "match_id",
            "event_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="RESTRICT"), nullable=False
    )
    event_key: Mapped[str] = mapped_column(String(120), nullable=False)
    sequence: Mapped[int] = mapped_column("event_sequence", Integer, nullable=False)
    minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )


class SimulationMarketControl(Base):
    __tablename__ = "simulation_market_controls"

    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT"), primary_key=True
    )
    manually_suspended: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    suspension_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )


class SimulationMarketState(Base):
    __tablename__ = "simulation_market_states"
    __table_args__ = (
        UniqueConstraint("run_id", "market_id", name="uq_simulation_market_state"),
        CheckConstraint(
            "status IN ('OPEN', 'SUSPENDED', 'CLOSED')",
            name="ck_simulation_market_state_status",
        ),
        CheckConstraint(
            "suspension_kind IN ('NONE', 'MANUAL', 'EVENT', 'CLOSED')",
            name="ck_simulation_market_state_suspension_kind",
        ),
        Index("ix_simulation_market_states_run_status", "run_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    suspension_kind: Mapped[str] = mapped_column(
        String(10), nullable=False, default="NONE", server_default="'NONE'"
    )
    suspension_reason: Mapped[str | None] = mapped_column(Text)
    reopen_after_minute: Mapped[int | None] = mapped_column(SmallInteger)


class SimulationSelectionQuote(Base):
    __tablename__ = "simulation_selection_quotes"
    __table_args__ = (
        CheckConstraint("decimal_odds > 1", name="ck_simulation_quote_odds"),
        CheckConstraint(
            "fair_probability BETWEEN 0 AND 1",
            name="ck_simulation_quote_probability",
        ),
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    selection_id: Mapped[int] = mapped_column(
        ForeignKey("market_selections.id", ondelete="RESTRICT"), primary_key=True
    )
    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT"), nullable=False
    )
    decimal_odds: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    fair_probability: Mapped[Decimal] = mapped_column(Numeric(10, 9), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("simulation_events.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )


class SimulationOddsHistory(Base):
    __tablename__ = "simulation_odds_history"
    __table_args__ = (
        CheckConstraint(
            "previous_odds > 1 AND current_odds > 1",
            name="ck_simulation_odds_history_valid_odds",
        ),
        CheckConstraint(
            "fair_probability BETWEEN 0 AND 1",
            name="ck_simulation_odds_history_probability",
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_simulation_odds_history_idempotency"
        ),
        Index("ix_simulation_odds_history_selection_time", "selection_id", "changed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT"), nullable=False
    )
    selection_id: Mapped[int] = mapped_column(
        ForeignKey("market_selections.id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("simulation_events.id", ondelete="RESTRICT")
    )
    previous_odds: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    current_odds: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    fair_probability: Mapped[Decimal] = mapped_column(Numeric(10, 9), nullable=False)
    percentage_change: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    cause_type: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)


class SimulationAuditLog(Base):
    __tablename__ = "simulation_audit_log"
    __table_args__ = (
        Index("ix_simulation_audit_created", "created_at"),
        Index("ix_simulation_audit_market_time", "market_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("simulation_runs.run_id", ondelete="RESTRICT")
    )
    match_id: Mapped[int | None] = mapped_column(
        ForeignKey("matches.id", ondelete="RESTRICT")
    )
    market_id: Mapped[int | None] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT")
    )
    selection_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_selections.id", ondelete="RESTRICT")
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("simulation_events.id", ondelete="RESTRICT")
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Simulation", server_default="'Simulation'"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True)


class TradingRiskIncident(Base):
    __tablename__ = "trading_risk_incidents"
    __table_args__ = (
        CheckConstraint(
            "highest_severity IN ('WARNING', 'ERROR')",
            name="ck_trading_risk_incidents_severity",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')",
            name="ck_trading_risk_incidents_status",
        ),
        CheckConstraint("occurrence_count > 0", name="ck_trading_risk_incidents_count"),
        CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_trading_risk_incidents_chronology",
        ),
        Index(
            "uq_trading_risk_incidents_open_key",
            "run_id",
            "match_id",
            "market_id",
            "selection_id",
            "rule_code",
            unique=True,
            sqlite_where=text("status = 'OPEN'"),
            postgresql_where=text("status = 'OPEN'"),
        ),
        Index("ix_trading_risk_incidents_status_severity", "status", "highest_severity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("simulation_runs.run_id", ondelete="RESTRICT")
    )
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="RESTRICT"), nullable=False
    )
    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT"), nullable=False
    )
    selection_id: Mapped[int] = mapped_column(
        ForeignKey("market_selections.id", ondelete="RESTRICT"), nullable=False
    )
    rule_code: Mapped[str] = mapped_column(String(40), nullable=False)
    highest_severity: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="OPEN", server_default="'OPEN'"
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledgement_note: Mapped[str | None] = mapped_column(Text)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TradingRiskOccurrence(Base):
    __tablename__ = "trading_risk_occurrences"
    __table_args__ = (
        UniqueConstraint("legacy_alert_id", name="uq_risk_occurrences_legacy_alert"),
        UniqueConstraint(
            "simulation_odds_history_id",
            name="uq_risk_occurrences_simulation_history",
        ),
        CheckConstraint(
            "(legacy_alert_id IS NOT NULL AND simulation_odds_history_id IS NULL) OR "
            "(legacy_alert_id IS NULL AND simulation_odds_history_id IS NOT NULL)",
            name="ck_risk_occurrences_one_source",
        ),
        CheckConstraint(
            "severity IN ('WARNING', 'ERROR')",
            name="ck_risk_occurrences_severity",
        ),
        Index("ix_risk_occurrences_incident_time", "incident_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("trading_risk_incidents.id", ondelete="RESTRICT"), nullable=False
    )
    legacy_alert_id: Mapped[int | None] = mapped_column(
        ForeignKey("trading_risk_alerts.id", ondelete="RESTRICT")
    )
    simulation_odds_history_id: Mapped[int | None] = mapped_column(
        ForeignKey("simulation_odds_history.id", ondelete="RESTRICT")
    )
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
