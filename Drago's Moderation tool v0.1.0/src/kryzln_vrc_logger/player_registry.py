from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Set

from .resolution import AvatarChange


@dataclass
class Player:
    username: str
    user_id: str = ""
    join_time: datetime = field(default_factory=datetime.now)
    platform: str = "Unknown"
    current_avatar: str = ""
    current_avatar_id: str = ""
    current_avatar_creator_id: str = ""
    last_avatar_lookup: float = 0.0
    last_platform_lookup: float = 0.0


class PlayerRegistry:
    def __init__(self):
        self.players: Dict[str, Player] = {}
        self.player_name_by_id: Dict[str, str] = {}
        self.players_needing_avatar_snapshot: Set[str] = set()

    def __len__(self) -> int:
        return len(self.players)

    def active_players(self):
        return self.players.values()

    def get(self, username: str) -> Optional[Player]:
        return self.players.get(username)

    def add(self, username: str, user_id: str, join_time: datetime) -> Player:
        player = Player(username=username, user_id=user_id, join_time=join_time)
        self.players[username] = player
        if user_id:
            self.player_name_by_id[user_id] = username
        return player

    def bind_user_id(self, player: Player, user_id: str):
        if not user_id:
            return
        player.user_id = user_id
        self.player_name_by_id[user_id] = player.username

    def remove(self, username: str, user_id: str) -> Optional[Player]:
        lookup_name = username
        if lookup_name not in self.players and user_id in self.player_name_by_id:
            lookup_name = self.player_name_by_id[user_id]

        player = self.players.pop(lookup_name, None)
        if player is None:
            return None

        self.players_needing_avatar_snapshot.discard(player.username)
        if player.user_id:
            self.player_name_by_id.pop(player.user_id, None)
        return player

    def mark_needing_avatar_snapshot(self, username: str):
        self.players_needing_avatar_snapshot.add(username)

    def clear_pending_avatar_snapshot(self, username: str):
        self.players_needing_avatar_snapshot.discard(username)

    def match_by_username(self, username: str) -> Optional[Player]:
        if username in self.players:
            return self.players[username]

        username_cf = username.casefold()
        for name, player in self.players.items():
            if name.casefold() == username_cf:
                return player

        return None

    def match_for_avatar_event(self, event: AvatarChange) -> Optional[Player]:
        if event.user_id and event.user_id in self.player_name_by_id:
            name = self.player_name_by_id[event.user_id]
            return self.players.get(name)

        return self.match_by_username(event.display_name)

    def clear(self):
        self.players.clear()
        self.player_name_by_id.clear()
        self.players_needing_avatar_snapshot.clear()
