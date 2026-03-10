import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import psutil

from .config import Config
from .log_parser import AvatarLogEvent, JoinLeaveEvent, LogParser
from .player_registry import Player, PlayerRegistry
from .printing import safe_print
from .resolution import AvatarChange, VRCXAvatarFeed
from .risk_engine import AvatarRiskStat, UserRiskStat
from .stability_monitor import StabilityMonitor
from .webhook import Discord


class VRCLogger:
    MAX_AVATAR_EVENT_CACHE = 5000
    AVATAR_LOOKUP_RETRY_SECONDS = 1.0
    AVATAR_LOOKUP_BATCH_SIZE = 8
    AVATAR_REFRESH_INTERVAL_SECONDS = 1.0
    PLATFORM_REFRESH_INTERVAL_SECONDS = 120.0
    PLATFORM_LOOKUP_BATCH_SIZE = 4

    MASS_LEAVE_WINDOW_SECONDS = 8.0
    MASS_LEAVE_MIN_COUNT = 4
    MASS_LEAVE_RATIO = 0.45
    MASS_LEAVE_SWITCH_LOOKBACK_SECONDS = 12.0

    RAPID_SWITCH_WINDOW_SECONDS = 8.0
    RAPID_SWITCH_MIN_COUNT = 5

    RED_HOLD_SECONDS = 15.0
    USER_JOIN_WINDOW_SECONDS = 600.0
    USER_SWITCH_WINDOW_SECONDS = 60.0
    STABILITY_WEBHOOK_COOLDOWN_SECONDS = 8.0

    def __init__(self):
        Config.init()

        self.log_parser = LogParser(Config.VRCHAT_LOG_DIR)
        self.discord = Discord(Config.DISCORD_WEBHOOK, printer=safe_print)
        self.vrcx_feed = VRCXAvatarFeed(Config.VRCX_DB_FILE or Path(), printer=safe_print)
        self.vrcx_feed.initialize_positions()

        self.player_registry = PlayerRegistry()
        self.last_world = ""
        self.running = False

        self.seen_avatar_events: Set[Tuple[str, str, str]] = set()
        self.avatar_event_order = deque()

        self.stability_monitor = StabilityMonitor(
            mass_leave_window_seconds=self.MASS_LEAVE_WINDOW_SECONDS,
            mass_leave_min_count=self.MASS_LEAVE_MIN_COUNT,
            mass_leave_ratio=self.MASS_LEAVE_RATIO,
            mass_leave_switch_lookback_seconds=self.MASS_LEAVE_SWITCH_LOOKBACK_SECONDS,
            rapid_switch_window_seconds=self.RAPID_SWITCH_WINDOW_SECONDS,
            rapid_switch_min_count=self.RAPID_SWITCH_MIN_COUNT,
            red_hold_seconds=self.RED_HOLD_SECONDS,
            user_join_window_seconds=self.USER_JOIN_WINDOW_SECONDS,
            user_switch_window_seconds=self.USER_SWITCH_WINDOW_SECONDS,
        )

        self.last_stability_webhook_at = 0.0
        self.startup_webhook_sent = False

    def find_vrchat(self) -> bool:
        try:
            for proc in psutil.process_iter(["name"]):
                name = proc.info.get("name")
                if name and name.lower() == Config.PROCESS_NAME.lower():
                    return True
        except (psutil.Error, OSError):
            return False
        return False

    def send_startup_webhook(self):
        if self.startup_webhook_sent:
            return

        self.startup_webhook_sent = True
        if not self.discord.enabled:
            safe_print("[!] Discord webhook disabled; use GUI Webhook URL field or set KRYZLN_DISCORD_WEBHOOK.")
            return

        fields = [
            {"name": "VRChat Logs", "value": f"`{Config.VRCHAT_LOG_DIR}`", "inline": False},
            {"name": "VRCX DB", "value": f"`{Config.VRCX_DB_FILE}`", "inline": False},
        ]

        ok = self.discord.send_embed(
            "Scanner Started",
            "Drago's Moderation Tool scanner is online.",
            0x1F6FEB,
            fields,
        )
        if ok:
            safe_print("[+] Webhook startup ping sent.")
        else:
            safe_print("[!] Webhook startup ping failed.")

    def _remember_avatar_event(self, key: Tuple[str, str, str]) -> bool:
        if key in self.seen_avatar_events:
            return False

        if len(self.avatar_event_order) >= self.MAX_AVATAR_EVENT_CACHE:
            old_key = self.avatar_event_order.popleft()
            self.seen_avatar_events.discard(old_key)

        self.avatar_event_order.append(key)
        self.seen_avatar_events.add(key)
        return True

    def _record_join_activity(self, username: str, user_id: str, join_time: datetime):
        self.stability_monitor.record_join(username, user_id, join_time)

    def _record_avatar_switch_activity(
        self,
        player: Player,
        event: AvatarChange,
    ):
        self.stability_monitor.record_avatar_switch(
            username=player.username,
            user_id=player.user_id,
            created_at=event.created_at,
            avatar_id=event.avatar_id or "",
            avatar_name=event.avatar_name or "",
            creator_id=event.owner_id or "",
        )

    def _record_leave_activity(self, player: Player, leave_time: datetime, players_before_leave: int):
        incident = self.stability_monitor.record_leave(
            username=player.username,
            user_id=player.user_id,
            leave_time=leave_time,
            players_before_leave=players_before_leave,
            avatar_id=player.current_avatar_id or "",
            avatar_name=player.current_avatar or "",
            creator_id=player.current_avatar_creator_id or "",
        )
        if incident is None:
            return

        self._log_line(
            f"MASS_LEAVE: leaves={incident.leave_count} baseline={incident.baseline} ratio={incident.leave_ratio:.2f}"
        )
        self._set_stability(
            "RED",
            incident.reason,
            fields=incident.fields,
            now=self.stability_monitor.last_mass_leave_at,
            force_discord=True,
        )

    def _top_avatar_risk(self, limit: int = 10) -> List[AvatarRiskStat]:
        return self.stability_monitor.top_avatar_risk(limit)

    def _top_user_risk(self, limit: int = 12) -> List[UserRiskStat]:
        return self.stability_monitor.top_user_risk(limit)

    def _set_stability(
        self,
        level: str,
        reason: str,
        fields: Optional[List[Dict[str, object]]] = None,
        now: Optional[float] = None,
        force_discord: bool = False,
    ):
        now_ts = now if now is not None else time.time()
        level_up = (level or "GREEN").upper()
        reason_text = reason or "stable"

        changed = (
            level_up != self.stability_monitor.stability_level
            or reason_text != self.stability_monitor.stability_reason
        )
        if not changed and not force_discord:
            return

        self.stability_monitor.stability_level = level_up
        self.stability_monitor.stability_reason = reason_text

        ts = datetime.now().strftime("%H:%M:%S")
        safe_print(f"[{ts}] STABILITY: {level_up} ({reason_text})")
        self._log_line(f"STABILITY: {level_up} ({reason_text})")

        if not self.discord.enabled:
            return

        cooldown_passed = (now_ts - self.last_stability_webhook_at) >= self.STABILITY_WEBHOOK_COOLDOWN_SECONDS
        if force_discord or changed or cooldown_passed:
            self.discord.stability(level_up, reason_text, fields)
            self.last_stability_webhook_at = now_ts

    def _evaluate_stability(self):
        now = time.time()
        snapshot = self.stability_monitor.evaluate_stability(now)
        self._set_stability(snapshot.level, snapshot.reason, now=now)

    def _apply_avatar_to_player(self, player: Player, event: AvatarChange, send_webhook: bool) -> bool:
        if not event.avatar_name:
            return False

        if event.user_id and not player.user_id:
            self.player_registry.bind_user_id(player, event.user_id)

        previous_avatar = player.current_avatar
        previous_avatar_id = player.current_avatar_id
        previous_creator_id = player.current_avatar_creator_id

        if previous_avatar == event.avatar_name:
            if event.avatar_id and not player.current_avatar_id:
                player.current_avatar_id = event.avatar_id
            if event.owner_id and not player.current_avatar_creator_id:
                player.current_avatar_creator_id = event.owner_id
            self.player_registry.clear_pending_avatar_snapshot(player.username)
            return False

        player.current_avatar = event.avatar_name
        player.current_avatar_id = event.avatar_id or ""
        player.current_avatar_creator_id = event.owner_id or ""
        self.player_registry.clear_pending_avatar_snapshot(player.username)

        # First avatar read should not count as a switch.
        if not previous_avatar:
            return True

        self._record_avatar_switch_activity(player, event)

        resolved_old_creator_id = previous_creator_id
        if previous_avatar_id and not resolved_old_creator_id:
            resolved_old_creator_id = self.vrcx_feed.resolve_avatar_creator(
                avatar_id=previous_avatar_id,
                avatar_name=previous_avatar,
                owner_hint=previous_creator_id,
            )

        resolved_new_creator_id = event.owner_id
        if event.avatar_id and not resolved_new_creator_id:
            resolved_new_creator_id = self.vrcx_feed.resolve_avatar_creator(
                avatar_id=event.avatar_id,
                avatar_name=event.avatar_name,
                owner_hint=event.owner_id,
            )
            event.owner_id = resolved_new_creator_id
            if resolved_new_creator_id:
                player.current_avatar_creator_id = resolved_new_creator_id

        meta_suffix = (
            f" [old_creator_id={resolved_old_creator_id or 'unknown'}]"
            f" [new_creator_id={resolved_new_creator_id or 'unknown'}]"
            f" [old_avatar_id={previous_avatar_id or 'unknown'}]"
            f" [new_avatar_id={event.avatar_id or 'unknown'}]"
        )

        ts = datetime.now().strftime("%H:%M:%S")
        safe_print(f"[{ts}] * {player.username} avatar: {previous_avatar} -> {event.avatar_name}")
        self._log_line(
            f"AVATAR_SWITCH: {player.username} ({player.user_id}) {previous_avatar} -> {event.avatar_name}{meta_suffix}"
        )
        if send_webhook:
            self.discord.avatar_change(
                username=player.username,
                old_avatar_name=previous_avatar,
                new_avatar_name=event.avatar_name,
                user_id=player.user_id,
                old_creator_id=resolved_old_creator_id,
                new_creator_id=resolved_new_creator_id,
                old_avatar_id=previous_avatar_id,
                new_avatar_id=event.avatar_id,
                created_at=event.created_at,
            )

        return True

    def _fetch_current_avatar_for_player(self, player: Player) -> bool:
        player.last_avatar_lookup = time.time()
        event = self.vrcx_feed.get_latest_avatar_for_player(player.user_id, player.username)
        if not event or not event.avatar_name:
            return False

        dedupe_key = (event.user_id or player.user_id, event.avatar_name, event.created_at)
        self._remember_avatar_event(dedupe_key)
        return self._apply_avatar_to_player(player, event, send_webhook=True)

    def _fetch_platform_for_player(self, player: Player) -> bool:
        if not player.user_id:
            return False

        player.last_platform_lookup = time.time()
        platform_label = self.vrcx_feed.resolve_user_platform(player.user_id)
        if not platform_label:
            return False

        if platform_label != player.platform:
            player.platform = platform_label
            ts = datetime.now().strftime("%H:%M:%S")
            safe_print(f"[{ts}] ~ {player.username} platform: {platform_label}")
            self._log_line(f"PLATFORM: {player.username} ({player.user_id}) -> {platform_label}")
        return True

    def _refresh_all_player_avatars(self):
        if not self.player_registry:
            return

        now = time.time()
        for player in self.player_registry.active_players():
            if player.last_avatar_lookup and (now - player.last_avatar_lookup) < self.AVATAR_REFRESH_INTERVAL_SECONDS:
                continue
            self._fetch_current_avatar_for_player(player)

    def _refresh_all_player_platforms(self):
        if not self.player_registry:
            return

        now = time.time()
        lookups = 0
        for player in self.player_registry.active_players():
            if lookups >= self.PLATFORM_LOOKUP_BATCH_SIZE:
                break
            if not player.user_id:
                continue
            if player.last_platform_lookup and (now - player.last_platform_lookup) < self.PLATFORM_REFRESH_INTERVAL_SECONDS:
                continue
            self._fetch_platform_for_player(player)
            lookups += 1

    def _hydrate_pending_avatars(self):
        if not self.player_registry.players_needing_avatar_snapshot:
            return

        now = time.time()
        lookups = 0
        for username in list(self.player_registry.players_needing_avatar_snapshot):
            if lookups >= self.AVATAR_LOOKUP_BATCH_SIZE:
                break

            player = self.player_registry.get(username)
            if not player:
                self.player_registry.clear_pending_avatar_snapshot(username)
                continue

            if player.current_avatar:
                self.player_registry.clear_pending_avatar_snapshot(username)
                continue

            if player.last_avatar_lookup and (now - player.last_avatar_lookup) < self.AVATAR_LOOKUP_RETRY_SECONDS:
                continue

            self._fetch_current_avatar_for_player(player)
            lookups += 1

    def add_player(self, username: str, user_id: str, join_time: datetime):
        existing_player = self.player_registry.get(username)
        if existing_player is not None:
            if user_id and not existing_player.user_id:
                self.player_registry.bind_user_id(existing_player, user_id)
                self._fetch_platform_for_player(existing_player)
            return

        player = self.player_registry.add(username, user_id, join_time)
        self._record_join_activity(username, user_id, join_time)
        self.player_registry.mark_needing_avatar_snapshot(username)

        ts = datetime.now().strftime("%H:%M:%S")
        safe_print(f"[{ts}] + {username}")
        self._log_line(f"JOIN: {username} ({user_id})")
        self.discord.player_join(username, user_id)

        self._fetch_current_avatar_for_player(player)
        self._fetch_platform_for_player(player)

    def remove_player(self, username: str, user_id: str, leave_time: Optional[datetime] = None):
        players_before_leave = len(self.player_registry)
        player = self.player_registry.remove(username, user_id)
        if player is None:
            return

        self._record_leave_activity(player, leave_time or datetime.now(), players_before_leave)

        ts = datetime.now().strftime("%H:%M:%S")
        safe_print(f"[{ts}] - {player.username}")
        self._log_line(f"LEAVE: {player.username} ({player.user_id})")
        self.discord.player_leave(player.username, player.user_id)

    def _match_player_by_username(self, username: str) -> Optional[Player]:
        return self.player_registry.match_by_username(username)

    def _match_player_for_avatar_event(self, event: AvatarChange) -> Optional[Player]:
        return self.player_registry.match_for_avatar_event(event)

    def process_log_avatar_changes(self, events: List[AvatarLogEvent]):
        if not events or not self.player_registry:
            return

        for ts, username, avatar_name, log_avatar_id in events:
            if not avatar_name:
                continue

            player = self._match_player_by_username(username)
            if not player:
                continue

            created_at = ts.strftime("%Y-%m-%d %H:%M:%S")
            dedupe_key = (player.user_id or player.username, avatar_name, created_at)
            if not self._remember_avatar_event(dedupe_key):
                continue

            owner_id, resolved_avatar_id = self.vrcx_feed.resolve_avatar_event_meta(
                user_id=player.user_id,
                display_name=player.username,
                avatar_name=avatar_name,
                event_created_at=created_at,
            )
            avatar_id = log_avatar_id or resolved_avatar_id
            creator_id = self.vrcx_feed.resolve_avatar_creator(
                avatar_id=avatar_id,
                avatar_name=avatar_name,
                owner_hint=owner_id,
            )

            event = AvatarChange(
                table_name="vrchat_log",
                row_id=0,
                created_at=created_at,
                user_id=player.user_id,
                display_name=player.username,
                avatar_name=avatar_name,
                owner_id=creator_id,
                avatar_id=avatar_id,
            )
            self._apply_avatar_to_player(player, event, send_webhook=True)

    def process_avatar_changes(self):
        events = self.vrcx_feed.poll()
        if not events or not self.player_registry:
            return

        for event in events:
            if not event.avatar_name:
                continue

            dedupe_key = (event.user_id or event.display_name, event.avatar_name, event.created_at)
            if not self._remember_avatar_event(dedupe_key):
                continue

            player = self._match_player_for_avatar_event(event)
            if not player:
                continue

            self._apply_avatar_to_player(player, event, send_webhook=True)

    def _log_line(self, message: str):
        try:
            with open(Config.SESSION_LOG, "a", encoding="utf-8") as f:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{ts}] {message}\n")
        except OSError:
            pass

    def _save_players(self):
        try:
            now = time.time()
            self.stability_monitor._prune_event_buffers(now)
            with open(Config.PLAYERS_FILE, "w", encoding="utf-8") as f:
                f.write("=" * 72 + "\n")
                f.write("  Drago's Moderation Tool - Active Players\n")
                f.write("=" * 72 + "\n")
                f.write(f"  World: {self.log_parser.current_world}\n")
                f.write(f"  Players: {len(self.player_registry)}\n")
                f.write(f"  Updated: {datetime.now()}\n")
                f.write(
                    "  STABILITY: "
                    f"{self.stability_monitor.stability_level} ({self.stability_monitor.stability_reason})\n"
                )
                f.write(f"  Mass Leave Incidents: {self.stability_monitor.mass_leave_incidents}\n")
                f.write("=" * 72 + "\n\n")

                for player in self.player_registry.active_players():
                    f.write(f"  {player.username}\n")
                    if player.user_id:
                        f.write(f"    User ID: {player.user_id}\n")
                    f.write(f"    Platform: {player.platform or 'Unknown'}\n")
                    if player.current_avatar:
                        f.write(f"    Avatar: {player.current_avatar}\n")
                        f.write(f"    Avatar Creator ID: {player.current_avatar_creator_id or '(unknown)'}\n")
                        f.write(f"    Avatar ID: {player.current_avatar_id or '(unknown)'}\n")
                    else:
                        f.write("    Avatar: (unknown yet)\n")
                    f.write("\n")

                f.write("=" * 72 + "\n")
                f.write("  Avatar Risk Table (top 10)\n")
                f.write("=" * 72 + "\n")
                avatar_rows = self._top_avatar_risk(10)
                if not avatar_rows:
                    f.write("  (no avatar risk data yet)\n\n")
                else:
                    for stat in avatar_rows:
                        correlation_pct = (stat.mass_leave_hits / max(1, stat.switches)) * 100.0
                        f.write(f"  {stat.avatar_id}\n")
                        f.write(f"    Name: {stat.avatar_name or '(unknown)'}\n")
                        f.write(f"    Creator: {stat.creator_id or '(unknown)'}\n")
                        f.write(
                            "    Switches: "
                            f"{stat.switches} | Leaves: {stat.leaves} | "
                            f"MassLeaveHits: {stat.mass_leave_hits} | Correlation: {correlation_pct:.1f}%\n"
                        )
                        f.write("\n")

                f.write("=" * 72 + "\n")
                f.write("  User Risk Table (top 12)\n")
                f.write("=" * 72 + "\n")
                user_rows = self._top_user_risk(12)
                if not user_rows:
                    f.write("  (no user risk data yet)\n")
                else:
                    for stat in user_rows:
                        self.stability_monitor._trim_user_windows(stat, now)
                        joins_recent = len(stat.join_times)
                        switches_recent = len(stat.switch_times)
                        crash_corr = stat.mass_leave_departures + stat.switches_before_mass_leave

                        f.write(f"  {stat.username}\n")
                        f.write(f"    User ID: {stat.user_id or '(unknown)'}\n")
                        f.write(
                            "    Join Frequency: "
                            f"{joins_recent} joins / {int(self.USER_JOIN_WINDOW_SECONDS)}s\n"
                        )
                        f.write(
                            "    Avatar Switch Rate: "
                            f"{switches_recent} switches / {int(self.USER_SWITCH_WINDOW_SECONDS)}s\n"
                        )
                        f.write(f"    Crash Correlation: {crash_corr}\n")
                        f.write(
                            "    Totals: "
                            f"joins={stat.total_joins}, "
                            f"switches={stat.total_avatar_switches}, "
                            f"mass_leave_departures={stat.mass_leave_departures}, "
                            f"switches_before_mass_leave={stat.switches_before_mass_leave}\n"
                        )
                        f.write("\n")
        except OSError:
            pass

    def _handle_world_change_if_needed(self):
        current_world = self.log_parser.current_world
        if current_world == self.last_world:
            return

        if current_world:
            safe_print(f"\n[WORLD] {current_world}\n")
            self.discord.world_change(current_world)
            self._log_line(f"WORLD: {current_world}")

        self.last_world = current_world
        self.player_registry.clear()
        self.stability_monitor.reset_recent_activity()
        self._set_stability("GREEN", "stable")

    def scan(self):
        vrchat_running = self.find_vrchat()

        if vrchat_running and not self.running:
            self.running = True
            safe_print("\n[!] VRChat started")
        elif not vrchat_running and self.running:
            self.running = False
            self.player_registry.clear()
            self.stability_monitor.reset_recent_activity()
            self.last_world = ""
            self._set_stability("GREEN", "idle (VRChat not running)")
            safe_print("\n[!] VRChat closed")
            self._save_players()
            return

        if not vrchat_running:
            return

        joins, leaves, log_avatar_events = self.log_parser.parse()
        self._handle_world_change_if_needed()

        for ts, username, user_id in joins:
            self.add_player(username, user_id, ts)

        for ts, username, user_id in leaves:
            self.remove_player(username, user_id, ts)

        self.process_log_avatar_changes(log_avatar_events)
        self._refresh_all_player_avatars()
        self._refresh_all_player_platforms()
        self.process_avatar_changes()
        self._evaluate_stability()
        self._save_players()

    def print_status(self):
        snapshot = self.get_status_snapshot()

        safe_print("=" * 72)
        safe_print("Drago's Moderation Tool")
        safe_print("=" * 72)
        safe_print(f"VRChat running: {'yes' if snapshot['running'] else 'no'}")
        safe_print(f"World: {snapshot['world']}")
        safe_print(f"Players tracked: {snapshot['players_tracked']}")
        safe_print(f"Avatars known: {snapshot['avatars_known']}")
        safe_print(f"STABILITY: {snapshot['stability_level']} ({snapshot['stability_reason']})")
        safe_print(
            f"Rapid avatar switches ({int(self.RAPID_SWITCH_WINDOW_SECONDS)}s): "
            f"{snapshot['rapid_switch_count']}"
        )
        safe_print(f"Mass leave incidents: {snapshot['mass_leave_incidents']}")
        safe_print(f"VRCX DB: {snapshot['vrcx_db']}")
        safe_print("=" * 72)

    def get_status_snapshot(self) -> Dict[str, object]:
        now = time.time()
        self.stability_monitor._prune_event_buffers(now)

        avatars_known = sum(1 for player in self.player_registry.active_players() if player.current_avatar)
        rapid_switch_count = self.stability_monitor.get_rapid_switch_count(now)

        player_rows: List[Dict[str, str]] = []
        for player in sorted(self.player_registry.active_players(), key=lambda p: p.username.casefold()):
            player_rows.append(
                {
                    "username": player.username,
                    "user_id": player.user_id or "",
                    "platform": player.platform or "Unknown",
                    "avatar": player.current_avatar or "(unknown yet)",
                    "avatar_id": player.current_avatar_id or "",
                    "creator_id": player.current_avatar_creator_id or "",
                }
            )

        user_risk_rows: List[Dict[str, object]] = []
        for stat in self._top_user_risk(12):
            self.stability_monitor._trim_user_windows(stat, now)
            crash_corr = stat.mass_leave_departures + stat.switches_before_mass_leave
            user_risk_rows.append(
                {
                    "username": stat.username,
                    "user_id": stat.user_id,
                    "joins_recent": len(stat.join_times),
                    "switches_recent": len(stat.switch_times),
                    "crash_correlation": crash_corr,
                    "total_joins": stat.total_joins,
                    "total_switches": stat.total_avatar_switches,
                }
            )

        return {
            "running": self.running,
            "world": self.log_parser.current_world or "(none)",
            "players_tracked": len(self.player_registry),
            "avatars_known": avatars_known,
            "stability_level": self.stability_monitor.stability_level,
            "stability_reason": self.stability_monitor.stability_reason,
            "rapid_switch_count": rapid_switch_count,
            "mass_leave_incidents": self.stability_monitor.mass_leave_incidents,
            "vrcx_db": str(Config.VRCX_DB_FILE or ""),
            "players": player_rows,
            "top_users": user_risk_rows,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }

    def run(self, stop_event: Optional[threading.Event] = None):
        safe_print("=" * 72)
        safe_print("Drago's Moderation Tool - Privacy Safe Edition")
        safe_print("=" * 72)
        safe_print("Tracking: world, players, avatar changes (VRChat + VRCX)")
        safe_print("Tracking: crash probability, avatar risk, user risk, stability levels")
        safe_print("No IP addresses or network connection scraping")
        safe_print(f"VRChat logs: {Config.VRCHAT_LOG_DIR}")
        safe_print(f"VRCX DB: {Config.VRCX_DB_FILE}")
        safe_print()
        self.send_startup_webhook()

        last_status = 0.0
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break

                try:
                    self.scan()
                except Exception as exc:
                    safe_print(f"[!] Error in scan: {exc}")

                now = time.time()
                if now - last_status >= Config.STATUS_INTERVAL:
                    self.print_status()
                    last_status = now

                if stop_event is not None:
                    if stop_event.wait(Config.SCAN_INTERVAL):
                        break
                else:
                    time.sleep(Config.SCAN_INTERVAL)
        except KeyboardInterrupt:
            safe_print("\nGoodbye")

