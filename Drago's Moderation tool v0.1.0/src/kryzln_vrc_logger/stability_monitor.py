import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, List, Optional, Set, Tuple

from .risk_engine import AvatarRiskStat, UserRiskStat


@dataclass(frozen=True)
class LeaveEvent:
    ts: float
    username: str
    user_id: str
    avatar_id: str = ""
    avatar_name: str = ""
    creator_id: str = ""
    players_before: int = 0


@dataclass(frozen=True)
class SwitchEvent:
    ts: float
    username: str
    user_id: str
    avatar_id: str = ""
    avatar_name: str = ""
    creator_id: str = ""


@dataclass
class MassLeaveIncident:
    leave_count: int
    baseline: int
    leave_ratio: float
    affected_users: List[str]
    affected_avatar_ids: List[str]
    reason: str

    @property
    def fields(self) -> List[Dict[str, object]]:
        fields: List[Dict[str, object]] = [
            {"name": "Leave Count", "value": str(self.leave_count), "inline": True},
            {"name": "Baseline Players", "value": str(self.baseline), "inline": True},
            {"name": "Leave Ratio", "value": f"{self.leave_ratio:.2f}", "inline": True},
        ]
        if self.affected_users:
            fields.append({"name": "Users Left", "value": ", ".join(self.affected_users[:20]), "inline": False})
        if self.affected_avatar_ids:
            fields.append(
                {
                    "name": "Avatars Correlated",
                    "value": ", ".join(self.affected_avatar_ids[:12]),
                    "inline": False,
                }
            )
        return fields


@dataclass
class StabilitySnapshot:
    level: str
    reason: str
    rapid_switch_count: int


class StabilityMonitor:
    def __init__(
        self,
        mass_leave_window_seconds: float = 8.0,
        mass_leave_min_count: int = 4,
        mass_leave_ratio: float = 0.45,
        mass_leave_switch_lookback_seconds: float = 12.0,
        rapid_switch_window_seconds: float = 8.0,
        rapid_switch_min_count: int = 5,
        red_hold_seconds: float = 15.0,
        user_join_window_seconds: float = 600.0,
        user_switch_window_seconds: float = 60.0,
    ):
        self.mass_leave_window_seconds = mass_leave_window_seconds
        self.mass_leave_min_count = mass_leave_min_count
        self.mass_leave_ratio = mass_leave_ratio
        self.mass_leave_switch_lookback_seconds = mass_leave_switch_lookback_seconds
        self.rapid_switch_window_seconds = rapid_switch_window_seconds
        self.rapid_switch_min_count = rapid_switch_min_count
        self.red_hold_seconds = red_hold_seconds
        self.user_join_window_seconds = user_join_window_seconds
        self.user_switch_window_seconds = user_switch_window_seconds

        self.avatar_risk: Dict[str, AvatarRiskStat] = {}
        self.user_risk: Dict[str, UserRiskStat] = {}
        self.recent_leaves: Deque[LeaveEvent] = deque()
        self.recent_switches: Deque[SwitchEvent] = deque()
        self.mass_leave_incidents = 0

        self.stability_level = "GREEN"
        self.stability_reason = "stable"
        self.last_mass_leave_at = 0.0
        self.last_mass_leave_reason = ""

    @staticmethod
    def parse_created_at_epoch(created_at: str) -> float:
        if not created_at:
            return time.time()

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S"):
            try:
                return datetime.strptime(created_at, fmt).timestamp()
            except ValueError:
                continue
        return time.time()

    @staticmethod
    def _user_risk_key(user_id: str, username: str) -> str:
        if user_id:
            return user_id
        return username.casefold()

    def _get_user_risk(self, user_id: str, username: str) -> UserRiskStat:
        key = self._user_risk_key(user_id, username)
        stat = self.user_risk.get(key)
        if stat is None:
            stat = UserRiskStat(user_key=key, username=username, user_id=user_id)
            self.user_risk[key] = stat
        else:
            if username:
                stat.username = username
            if user_id:
                stat.user_id = user_id
        return stat

    def _get_avatar_risk(self, avatar_id: str, avatar_name: str = "", creator_id: str = "") -> Optional[AvatarRiskStat]:
        if not avatar_id or not avatar_id.startswith("avtr_"):
            return None

        stat = self.avatar_risk.get(avatar_id)
        if stat is None:
            stat = AvatarRiskStat(avatar_id=avatar_id, avatar_name=avatar_name, creator_id=creator_id)
            self.avatar_risk[avatar_id] = stat
        else:
            if avatar_name:
                stat.avatar_name = avatar_name
            if creator_id:
                stat.creator_id = creator_id
        return stat

    def _trim_user_windows(self, stat: UserRiskStat, now: float):
        join_cutoff = now - self.user_join_window_seconds
        while stat.join_times and stat.join_times[0] < join_cutoff:
            stat.join_times.popleft()

        switch_cutoff = now - self.user_switch_window_seconds
        while stat.switch_times and stat.switch_times[0] < switch_cutoff:
            stat.switch_times.popleft()

    def _prune_event_buffers(self, now: float):
        leave_cutoff = now - self.mass_leave_window_seconds
        while self.recent_leaves and self.recent_leaves[0].ts < leave_cutoff:
            self.recent_leaves.popleft()

        switch_cutoff = now - max(self.rapid_switch_window_seconds, self.mass_leave_switch_lookback_seconds)
        while self.recent_switches and self.recent_switches[0].ts < switch_cutoff:
            self.recent_switches.popleft()

    def record_join(self, username: str, user_id: str, join_time: datetime):
        ts = join_time.timestamp() if isinstance(join_time, datetime) else time.time()
        stat = self._get_user_risk(user_id, username)
        stat.total_joins += 1
        stat.join_times.append(ts)
        self._trim_user_windows(stat, time.time())

    def record_avatar_switch(
        self,
        username: str,
        user_id: str,
        created_at: str,
        avatar_id: str = "",
        avatar_name: str = "",
        creator_id: str = "",
    ):
        now = self.parse_created_at_epoch(created_at)

        user_stat = self._get_user_risk(user_id, username)
        user_stat.total_avatar_switches += 1
        user_stat.switch_times.append(now)
        self._trim_user_windows(user_stat, now)

        self.recent_switches.append(
            SwitchEvent(
                ts=now,
                username=username,
                user_id=user_id,
                avatar_id=avatar_id or "",
                avatar_name=avatar_name or "",
                creator_id=creator_id or "",
            )
        )
        self._prune_event_buffers(now)

        avatar_stat = self._get_avatar_risk(avatar_id or "", avatar_name or "", creator_id or "")
        if avatar_stat is not None:
            avatar_stat.switches += 1

    def record_leave(
        self,
        username: str,
        user_id: str,
        leave_time: datetime,
        players_before_leave: int,
        avatar_id: str = "",
        avatar_name: str = "",
        creator_id: str = "",
    ) -> Optional[MassLeaveIncident]:
        now = leave_time.timestamp() if isinstance(leave_time, datetime) else time.time()

        self.recent_leaves.append(
            LeaveEvent(
                ts=now,
                username=username,
                user_id=user_id,
                avatar_id=avatar_id or "",
                avatar_name=avatar_name or "",
                creator_id=creator_id or "",
                players_before=players_before_leave,
            )
        )
        self._prune_event_buffers(now)

        avatar_stat = self._get_avatar_risk(avatar_id or "", avatar_name or "", creator_id or "")
        if avatar_stat is not None:
            avatar_stat.leaves += 1

        return self.detect_mass_leave(now)

    def detect_mass_leave(self, now: Optional[float] = None) -> Optional[MassLeaveIncident]:
        now_ts = time.time() if now is None else now
        self._prune_event_buffers(now_ts)

        leave_count = len(self.recent_leaves)
        if leave_count < self.mass_leave_min_count:
            return None

        baseline = max((evt.players_before for evt in self.recent_leaves), default=0)
        baseline = max(baseline, leave_count)
        leave_ratio = leave_count / float(max(1, baseline))
        if leave_ratio < self.mass_leave_ratio:
            return None

        if self.last_mass_leave_at and (now_ts - self.last_mass_leave_at) < (self.mass_leave_window_seconds * 0.5):
            return None

        self.last_mass_leave_at = now_ts
        self.mass_leave_incidents += 1

        affected_users: List[str] = []
        affected_avatar_ids: Set[str] = set()

        for evt in self.recent_leaves:
            user_stat = self._get_user_risk(evt.user_id, evt.username)
            user_stat.mass_leave_departures += 1
            affected_users.append(evt.username)

            avatar_stat = self._get_avatar_risk(evt.avatar_id, evt.avatar_name, evt.creator_id)
            if avatar_stat is not None:
                avatar_stat.mass_leave_hits += 1
                affected_avatar_ids.add(evt.avatar_id)

        for evt in self.recent_switches:
            if now_ts - evt.ts > self.mass_leave_switch_lookback_seconds:
                continue
            if evt.ts > now_ts + 1.0:
                continue

            user_stat = self._get_user_risk(evt.user_id, evt.username)
            user_stat.switches_before_mass_leave += 1

            avatar_stat = self._get_avatar_risk(evt.avatar_id, evt.avatar_name, evt.creator_id)
            if avatar_stat is not None:
                avatar_stat.mass_leave_hits += 1
                affected_avatar_ids.add(evt.avatar_id)

        reason = (
            f"mass leave detected ({leave_count}/{baseline} players in "
            f"{int(self.mass_leave_window_seconds)}s)"
        )
        self.last_mass_leave_reason = reason

        return MassLeaveIncident(
            leave_count=leave_count,
            baseline=baseline,
            leave_ratio=leave_ratio,
            affected_users=affected_users,
            affected_avatar_ids=sorted(affected_avatar_ids),
            reason=reason,
        )

    def get_rapid_switch_count(self, now: Optional[float] = None) -> int:
        now_ts = time.time() if now is None else now
        self._prune_event_buffers(now_ts)
        return sum(1 for evt in self.recent_switches if (now_ts - evt.ts) <= self.rapid_switch_window_seconds)

    def evaluate_stability(self, now: Optional[float] = None) -> StabilitySnapshot:
        now_ts = time.time() if now is None else now
        self._prune_event_buffers(now_ts)

        if self.last_mass_leave_at and (now_ts - self.last_mass_leave_at) <= self.red_hold_seconds:
            return StabilitySnapshot("RED", self.last_mass_leave_reason or "mass leave detected", 0)

        rapid_switch_count = self.get_rapid_switch_count(now_ts)
        if rapid_switch_count >= self.rapid_switch_min_count:
            return StabilitySnapshot(
                "YELLOW",
                f"rapid avatar switches ({rapid_switch_count} in {int(self.rapid_switch_window_seconds)}s)",
                rapid_switch_count,
            )

        return StabilitySnapshot("GREEN", "stable", rapid_switch_count)

    def top_avatar_risk(self, limit: int = 10) -> List[AvatarRiskStat]:
        stats = [stat for stat in self.avatar_risk.values() if stat.switches or stat.leaves or stat.mass_leave_hits]
        stats.sort(key=lambda stat: (stat.mass_leave_hits, stat.leaves, stat.switches), reverse=True)
        return stats[:limit]

    def top_user_risk(self, limit: int = 12) -> List[UserRiskStat]:
        now = time.time()
        stats = list(self.user_risk.values())
        for stat in stats:
            self._trim_user_windows(stat, now)

        def sort_key(stat: UserRiskStat) -> Tuple[int, int, int, int]:
            crash_corr = stat.mass_leave_departures + stat.switches_before_mass_leave
            return (
                crash_corr,
                len(stat.switch_times),
                stat.total_avatar_switches,
                len(stat.join_times),
            )

        stats.sort(key=sort_key, reverse=True)
        return stats[:limit]

    def reset_recent_activity(self):
        self.recent_leaves.clear()
        self.recent_switches.clear()
        self.last_mass_leave_at = 0.0
        self.last_mass_leave_reason = ""
