import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional, Set, Tuple, TypeAlias


JoinLeaveEvent: TypeAlias = Tuple[datetime, str, str]
AvatarLogEvent: TypeAlias = Tuple[datetime, str, str, str]


class LogParser:
    # Parse VRChat output logs for world, player, and avatar events.

    JOIN_PATTERN = re.compile(
        r"(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}).*?\[Behaviour\] OnPlayerJoined (.+?) \((usr_[a-f0-9\-]+)\)",
        re.MULTILINE,
    )
    LEAVE_PATTERN = re.compile(
        r"(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}).*?\[Behaviour\] OnPlayerLeft (.+?) \((usr_[a-f0-9\-]+)\)",
        re.MULTILINE,
    )
    AVATAR_SWITCH_PATTERN = re.compile(
        r"(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}).*?\[Behaviour\] Switching (.+?) to avatar (.+)",
        re.MULTILINE,
    )
    AVATAR_SAVE_PATTERN = re.compile(
        r"(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}).*?(?:Saving|Loading) Avatar Data:(avtr_[a-f0-9\-]+)",
        re.MULTILINE,
    )
    WORLD_PATTERN = re.compile(r"\[Behaviour\] Entering Room: (.+)", re.MULTILINE)
    AUTH_PATTERN = re.compile(
        r"(?:\[Behaviour\]\s*)?User Authenticated: (.+?) \((usr_[a-f0-9\-]+)\)",
        re.MULTILINE,
    )
    AUTH_PATTERN_FALLBACK = re.compile(r"(?:\[Behaviour\]\s*)?User Authenticated: (.+)", re.MULTILINE)
    PRIME_READ_BYTES = 2_000_000

    def __init__(self, log_dir: Optional[Path] = None):
        self.log_file: Optional[Path] = None
        self.log_dir = log_dir
        self.position = 0
        self.current_world = ""
        self.my_username = ""
        self.my_user_id = ""

    def find_log(self) -> Optional[Path]:
        try:
            if not self.log_dir or not self.log_dir.exists():
                return None
            logs = list(self.log_dir.glob("output_log_*.txt"))
            if not logs:
                return None
            return max(logs, key=lambda f: f.stat().st_mtime)
        except OSError:
            return None

    def _latest_world_section(self, content: str) -> str:
        last_world_match = None
        for match in self.WORLD_PATTERN.finditer(content):
            last_world_match = match
        return content[last_world_match.start() :] if last_world_match else content

    def _update_auth_and_world(self, content: str):
        saw_auth = False
        for match in self.AUTH_PATTERN.finditer(content):
            self.my_username = match.group(1).strip()
            self.my_user_id = match.group(2).strip()
            saw_auth = True

        if not saw_auth:
            for match in self.AUTH_PATTERN_FALLBACK.finditer(content):
                auth_text = match.group(1).strip()
                parsed = re.match(r"(.+?) \((usr_[a-f0-9\-]+)\)$", auth_text)
                if parsed:
                    self.my_username = parsed.group(1).strip()
                    self.my_user_id = parsed.group(2).strip()
                else:
                    self.my_username = auth_text
                    self.my_user_id = ""

        for match in self.WORLD_PATTERN.finditer(content):
            self.current_world = match.group(1).strip()

    def _is_self(self, username: str, user_id: str) -> bool:
        if self.my_user_id and user_id == self.my_user_id:
            return True
        if self.my_username and username == self.my_username:
            return True
        return False

    def _reconstruct_current_players(self, content: str) -> List[JoinLeaveEvent]:
        # Rebuild active roster from the latest world segment so startup includes users already present.
        section = self._latest_world_section(content)
        timeline = []

        for match in self.JOIN_PATTERN.finditer(section):
            timeline.append((match.start(), "join", match))
        for match in self.LEAVE_PATTERN.finditer(section):
            timeline.append((match.start(), "leave", match))

        timeline.sort(key=lambda item: item[0])

        active_players_by_id: Dict[str, JoinLeaveEvent] = {}
        for _, event_type, match in timeline:
            try:
                ts = datetime.strptime(match.group(1), "%Y.%m.%d %H:%M:%S")
                username = match.group(2).strip()
                user_id = match.group(3).strip()
            except (ValueError, IndexError):
                continue

            if self._is_self(username, user_id):
                continue

            if event_type == "join":
                active_players_by_id[user_id] = (ts, username, user_id)
            else:
                active_players_by_id.pop(user_id, None)

        return sorted(active_players_by_id.values(), key=lambda event: event[0])

    def _extract_avatar_switches_with_ids(
        self, content: str, active_usernames: Optional[Set[str]] = None
    ) -> List[AvatarLogEvent]:
        active_names_cf = {name.casefold() for name in (active_usernames or set()) if name}
        timeline = []

        for match in self.AVATAR_SWITCH_PATTERN.finditer(content):
            try:
                ts = datetime.strptime(match.group(1), "%Y.%m.%d %H:%M:%S")
                username = match.group(2).strip()
                avatar_name = match.group(3).strip()
            except (ValueError, IndexError):
                continue

            if not avatar_name:
                continue
            if self.my_username and username == self.my_username:
                continue
            if active_names_cf and username.casefold() not in active_names_cf:
                continue

            timeline.append((match.start(), "switch", ts, username, avatar_name))

        for match in self.AVATAR_SAVE_PATTERN.finditer(content):
            try:
                ts = datetime.strptime(match.group(1), "%Y.%m.%d %H:%M:%S")
                avatar_id = match.group(2).strip()
            except (ValueError, IndexError):
                continue

            if not avatar_id:
                continue
            timeline.append((match.start(), "save", ts, "", avatar_id))

        timeline.sort(key=lambda item: item[0])

        switches: List[List[object]] = []
        pending_indices: Deque[int] = deque()

        for _, event_type, ts, event_subject, event_value in timeline:
            if event_type == "switch":
                switches.append([ts, str(event_subject), str(event_value), ""])
                pending_indices.append(len(switches) - 1)
                continue

            avatar_id = str(event_value)
            while pending_indices:
                first_idx = pending_indices[0]
                first_ts = switches[first_idx][0]
                if isinstance(first_ts, datetime) and (ts - first_ts).total_seconds() > 20:
                    pending_indices.popleft()
                else:
                    break

            for idx in reversed(pending_indices):
                switch_ts = switches[idx][0]
                current_id = switches[idx][3]
                if current_id:
                    continue
                if not isinstance(switch_ts, datetime):
                    continue

                age = (ts - switch_ts).total_seconds()
                if age < -1 or age > 15:
                    continue

                switches[idx][3] = avatar_id
                break

        return [(s[0], s[1], s[2], s[3]) for s in switches]

    def _reconstruct_current_avatars(
        self, content: str, active_usernames: Set[str]
    ) -> List[AvatarLogEvent]:
        # Rebuild latest avatar state from VRChat logs for users currently active in the latest world segment.
        section = self._latest_world_section(content)
        latest_by_name: Dict[str, AvatarLogEvent] = {}

        for event in self._extract_avatar_switches_with_ids(section, active_usernames):
            _, username, _, _ = event
            latest_by_name[username.casefold()] = event

        return sorted(latest_by_name.values(), key=lambda event: event[0])

    def _prime_state_from_existing_log(
        self, log: Path
    ) -> Tuple[List[JoinLeaveEvent], List[AvatarLogEvent]]:
        # Read the tail of an existing log so world/auth state is known, then reconstruct roster + avatars.
        content = ""
        try:
            size = log.stat().st_size
            with open(log, "r", encoding="utf-8", errors="ignore") as f:
                if size > self.PRIME_READ_BYTES:
                    f.seek(size - self.PRIME_READ_BYTES)
                content = f.read()
                self.position = f.tell()
        except OSError:
            return [], []

        self._update_auth_and_world(content)
        bootstrap_joins = self._reconstruct_current_players(content)
        active_names = {username for _, username, _ in bootstrap_joins}
        bootstrap_avatars = self._reconstruct_current_avatars(content, active_names)
        return bootstrap_joins, bootstrap_avatars

    def parse(
        self,
    ) -> Tuple[List[JoinLeaveEvent], List[JoinLeaveEvent], List[AvatarLogEvent]]:
        log = self.find_log()
        if not log:
            return [], [], []

        if log != self.log_file:
            self.log_file = log
            self.position = 0
            bootstrap_joins, bootstrap_avatars = self._prime_state_from_existing_log(log)
            return bootstrap_joins, [], bootstrap_avatars

        try:
            with open(log, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(self.position)
                content = f.read()
                self.position = f.tell()
        except OSError:
            return [], [], []

        if not content:
            return [], [], []

        joins: List[JoinLeaveEvent] = []
        leaves: List[JoinLeaveEvent] = []
        avatar_switches: List[AvatarLogEvent] = []

        self._update_auth_and_world(content)

        for match in self.JOIN_PATTERN.finditer(content):
            try:
                ts = datetime.strptime(match.group(1), "%Y.%m.%d %H:%M:%S")
                username = match.group(2).strip()
                user_id = match.group(3).strip()
                if not self._is_self(username, user_id):
                    joins.append((ts, username, user_id))
            except (ValueError, IndexError):
                continue

        for match in self.LEAVE_PATTERN.finditer(content):
            try:
                ts = datetime.strptime(match.group(1), "%Y.%m.%d %H:%M:%S")
                username = match.group(2).strip()
                user_id = match.group(3).strip()
                if not self._is_self(username, user_id):
                    leaves.append((ts, username, user_id))
            except (ValueError, IndexError):
                continue

        avatar_switches.extend(self._extract_avatar_switches_with_ids(content))

        return joins, leaves, avatar_switches


