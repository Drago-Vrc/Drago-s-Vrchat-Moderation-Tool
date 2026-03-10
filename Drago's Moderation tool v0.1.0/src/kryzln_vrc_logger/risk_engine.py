from collections import deque
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class AvatarRiskStat:
    avatar_id: str
    avatar_name: str = ""
    creator_id: str = ""
    switches: int = 0
    leaves: int = 0
    mass_leave_hits: int = 0


@dataclass
class UserRiskStat:
    user_key: str
    username: str
    user_id: str = ""
    total_joins: int = 0
    total_avatar_switches: int = 0
    mass_leave_departures: int = 0
    mass_leave_presence: int = 0
    switches_before_mass_leave: int = 0
    join_times: Deque[float] = field(default_factory=deque)
    switch_times: Deque[float] = field(default_factory=deque)

