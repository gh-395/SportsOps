from sportsops.database.service import DatabaseService
from sportsops.models.simulation import SimulationRunInfo
from sportsops.models.trading import RiskThresholds


class MarketSimulationController:
    def __init__(
        self,
        database: DatabaseService,
        thresholds: RiskThresholds | None = None,
    ) -> None:
        self.database = database
        self.thresholds = thresholds or RiskThresholds()
        self.speed = 5
        self._running = False
        self._run: SimulationRunInfo | None = database.load_latest_simulation_run()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def started(self) -> bool:
        return self._run is not None

    @property
    def run(self) -> SimulationRunInfo | None:
        return self._run

    @property
    def run_id(self) -> str | None:
        return self._run.run_id if self._run else None

    def start(self, seed: int = 20261115, speed: int | None = None) -> None:
        self.speed = speed or self.speed
        self._run = self.database.start_event_simulation(
            seed, self.speed, self.thresholds
        )
        self.speed = self._run.speed
        self._running = self._run.status == "RUNNING"

    def pause(self) -> None:
        if self._run is None or not self._running:
            return
        self.database.pause_event_simulation(self._run.run_id)
        self._running = False
        self._run = self.database.load_latest_simulation_run()

    def set_speed(self, speed: int) -> None:
        if speed not in (1, 5, 10):
            raise ValueError("Simulation speed must be 1x, 5x, or 10x.")
        if self._run is not None:
            self.database.set_event_simulation_speed(self._run.run_id, speed)
            self._run = self.database.load_latest_simulation_run()
        self.speed = speed

    def set_thresholds(self, thresholds: RiskThresholds) -> None:
        self.thresholds = thresholds
        if self._run is not None and self._run.status in ("RUNNING", "PAUSED"):
            self.database.set_event_simulation_thresholds(
                self._run.run_id, thresholds
            )
            self._run = self.database.load_latest_simulation_run()

    def advance(self) -> int:
        if not self._running or self._run is None:
            return 0
        changes = self.database.advance_event_simulation(self._run.run_id)
        self._run = self.database.load_latest_simulation_run()
        if self._run is None or self._run.status != "RUNNING":
            self._running = False
        return changes

    def simulate_risk_event(self, selection_id: int) -> str:
        if self._run is None or not self._running:
            raise ValueError("Start or resume a simulation first.")
        return self.database.simulate_risk_event(self._run.run_id, selection_id)

    def halt_after_error(self) -> None:
        self._running = False

    def reset(self) -> bool:
        self._running = False
        if self._run is None:
            return False
        changed = self.database.reset_event_simulation(self._run.run_id)
        self._run = None
        return changed
