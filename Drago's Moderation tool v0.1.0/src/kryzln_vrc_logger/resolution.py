import base64
import binascii
import json
import os
import re
import sqlite3
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

import requests

from .config import env_flag


@dataclass
class AvatarChange:
    table_name: str
    row_id: int
    created_at: str
    user_id: str
    display_name: str
    avatar_name: str
    owner_id: str
    avatar_id: str = ""


class VRCXAvatarFeed:
    def __init__(self, db_path: Path, printer: Callable[..., object] = print):
        self.db_path = db_path
        self.last_seen_id_by_table: Dict[str, int] = {}
        self._print = printer
        self._read_connection: Optional[sqlite3.Connection] = None
        self._feed_tables_cache: Optional[List[str]] = None

        self.enable_vrchat_api_fallback = env_flag("KRYZLN_VRCHAT_API_FALLBACK", True)
        self.api_user_agent = os.environ.get(
            "KRYZLN_VRCHAT_USER_AGENT",
            "DragosModerationTool/0.1.0",
        )
        self.api_timeout_seconds = 8.0
        self.enable_website_search_fallback = env_flag("KRYZLN_WEBSITE_SEARCH_FALLBACK", True)
        self.website_search_timeout_seconds = 8.0
        self._api_backoff_until = 0.0

        self._api_avatar_meta_cache: Dict[str, Tuple[str, str, str, str]] = {}
        self._api_search_cache: Dict[Tuple[str, str], Tuple[str, str]] = {}
        self._api_user_platform_cache: Dict[str, Tuple[str, float]] = {}
        self._api_user_platform_cache_ttl_seconds = 180.0
        self._website_search_cache: Dict[Tuple[str, str], str] = {}

    def close(self):
        if self._read_connection is None:
            return
        try:
            self._read_connection.close()
        except sqlite3.Error:
            pass
        finally:
            self._read_connection = None
            self._feed_tables_cache = None

    def __del__(self):
        self.close()

    def _reset_read_connection(self):
        self.close()

    def _get_connection(self) -> Optional[sqlite3.Connection]:
        if not self.db_path.exists():
            self.close()
            return None

        if self._read_connection is not None:
            return self._read_connection

        try:
            db_uri = self.db_path.resolve().as_uri() + "?mode=ro"
            self._read_connection = sqlite3.connect(db_uri, timeout=1, uri=True, check_same_thread=False)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self._print(f"[~] read-only DB open failed: {exc}")
            try:
                self._read_connection = sqlite3.connect(str(self.db_path), timeout=1, check_same_thread=False)
            except sqlite3.Error:
                self._read_connection = None

        return self._read_connection

    def _get_cursor(self) -> Optional[sqlite3.Cursor]:
        con = self._get_connection()
        if con is None:
            return None
        return con.cursor()

    @staticmethod
    def _normalize_user_platform(last_platform: str = "", platform: str = "") -> str:
        raw = (last_platform or platform or "").strip().lower()
        if not raw:
            return "Unknown"

        if "android" in raw or "quest" in raw:
            return "Quest"
        if "standalonewindows" in raw or "windows" in raw or "steam" in raw or raw == "pc":
            return "PC"
        return "Unknown"

    def _read_api_cookies(self, cur: sqlite3.Cursor) -> Dict[str, str]:
        if not self.enable_vrchat_api_fallback:
            return {}
        try:
            row = cur.execute("SELECT value FROM [cookies] WHERE key = 'default' LIMIT 1").fetchone()
            raw = str(row[0] or "") if row else ""
            if not raw:
                return {}

            decoded = base64.b64decode(raw).decode("utf-8", "ignore")
            items = json.loads(decoded)
            if not isinstance(items, list):
                return {}

            cookies: Dict[str, str] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("Name") or "").strip()
                value = str(item.get("Value") or "").strip()
                domain = str(item.get("Domain") or "").strip().lower()
                if not name or not value:
                    continue
                if item.get("Expired"):
                    continue
                if "vrchat" not in domain:
                    continue
                cookies[name] = value

            return cookies if "auth" in cookies else {}
        except (binascii.Error, json.JSONDecodeError, TypeError, ValueError, sqlite3.Error):
            return {}

    @staticmethod
    def _extract_avtr_ids_from_text(text: str) -> List[str]:
        if not text:
            return []

        ids = re.findall(
            r"(avtr_[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})",
            text,
            flags=re.I,
        )
        out: List[str] = []
        seen: Set[str] = set()
        for avatar_id in ids:
            normalized = avatar_id.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out

    def _search_avatar_id_from_avtrdb(self, avatar_name: str, owner_id: str = "") -> str:
        if not avatar_name:
            return ""

        query = f"name:{avatar_name}"
        params = {
            "page_size": "14",
            "page": "0",
            "query": query,
        }
        url = "https://api.avtrdb.com/v2/avatar/search"

        try:
            resp = requests.get(
                url,
                params=params,
                headers={"User-Agent": self.api_user_agent},
                timeout=self.website_search_timeout_seconds,
            )
            if resp.status_code != 200:
                return ""

            payload = resp.json()
            avatars = payload.get("avatars") if isinstance(payload, dict) else None
            if not isinstance(avatars, list):
                return ""

            wanted_name = avatar_name.casefold()
            wanted_owner = (owner_id or "").strip().casefold()
            best_id = ""
            best_rank = -1

            for item in avatars:
                if not isinstance(item, dict):
                    continue

                candidate_id = str(item.get("vrc_id") or "").strip().lower()
                if not candidate_id.startswith("avtr_"):
                    continue

                candidate_name = str(item.get("name") or "").strip()
                author = item.get("author") if isinstance(item.get("author"), dict) else {}
                candidate_author_id = str(author.get("vrc_id") or "").strip().casefold()

                exact_name = bool(candidate_name) and candidate_name.casefold() == wanted_name
                owner_match = bool(wanted_owner) and candidate_author_id == wanted_owner
                partial_name = wanted_name in candidate_name.casefold() if candidate_name else False

                if exact_name and owner_match:
                    rank = 4
                elif exact_name:
                    rank = 3
                elif partial_name and owner_match:
                    rank = 2
                elif partial_name:
                    rank = 1
                else:
                    continue

                if rank > best_rank:
                    best_rank = rank
                    best_id = candidate_id

            return best_id
        except (requests.RequestException, ValueError):
            return ""

    def _search_avatar_id_from_vrcdb(self, avatar_name: str) -> str:
        if not avatar_name:
            return ""

        encoded = urllib.parse.quote(avatar_name)
        candidates = [
            f"https://vrcdb.com/?search={encoded}",
            f"https://vrcdb.com/search/{encoded}",
            f"https://vrcdb.com/search?query={encoded}",
        ]

        for url in candidates:
            try:
                resp = requests.get(
                    url,
                    headers={"User-Agent": self.api_user_agent},
                    timeout=self.website_search_timeout_seconds,
                )
                if resp.status_code != 200:
                    continue
                avatar_ids = self._extract_avtr_ids_from_text(resp.text or "")
                if avatar_ids:
                    return avatar_ids[0]
            except requests.RequestException:
                continue

        return ""

    def _search_avatar_id_from_websites(self, avatar_name: str, owner_id: str = "") -> str:
        if not self.enable_website_search_fallback:
            return ""
        if not avatar_name:
            return ""

        cache_key = (avatar_name.casefold(), owner_id or "")
        if cache_key in self._website_search_cache:
            return self._website_search_cache[cache_key]

        avatar_id = self._search_avatar_id_from_avtrdb(avatar_name, owner_id) or self._search_avatar_id_from_vrcdb(
            avatar_name
        )
        self._website_search_cache[cache_key] = avatar_id
        if avatar_id:
            self._print(f"[~] Website search matched avatar '{avatar_name}' -> {avatar_id}")
        return avatar_id

    def _api_get_json(
        self,
        cur: sqlite3.Cursor,
        path: str,
        params: Optional[Dict[str, str]] = None,
    ) -> Optional[object]:
        if not self.enable_vrchat_api_fallback:
            return None

        now = time.time()
        if now < self._api_backoff_until:
            return None

        cookies = self._read_api_cookies(cur)
        if not cookies:
            return None

        url = f"https://api.vrchat.cloud/api/1/{path.lstrip('/')}"
        headers = {"User-Agent": self.api_user_agent}

        try:
            resp = requests.get(url, params=params, headers=headers, cookies=cookies, timeout=self.api_timeout_seconds)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (401, 403, 429):
                self._api_backoff_until = now + 60.0
            return None
        except requests.RequestException as exc:
            self._api_backoff_until = now + 15.0
            self._print(f"[~] VRChat API request failed: {exc}")
            return None
        except ValueError:
            self._api_backoff_until = now + 15.0
            return None

    def _api_avatar_meta(self, cur: sqlite3.Cursor, avatar_id: str) -> Tuple[str, str, str, str]:
        if not avatar_id or not avatar_id.startswith("avtr_"):
            return "", "", "", ""

        if avatar_id in self._api_avatar_meta_cache:
            return self._api_avatar_meta_cache[avatar_id]

        data = self._api_get_json(cur, f"avatars/{avatar_id}")
        if not isinstance(data, dict):
            return "", "", "", ""

        creator_id = str(data.get("authorId") or "")
        avatar_name = str(data.get("name") or "")
        image_url = str(data.get("imageUrl") or "")
        thumbnail_url = str(data.get("thumbnailImageUrl") or "")

        meta = (creator_id, avatar_name, image_url, thumbnail_url)
        self._api_avatar_meta_cache[avatar_id] = meta
        return meta

    def _api_search_avatar(self, cur: sqlite3.Cursor, avatar_name: str, owner_id: str = "") -> Tuple[str, str]:
        if not avatar_name:
            return "", ""

        key = (avatar_name.casefold(), owner_id or "")
        if key in self._api_search_cache:
            return self._api_search_cache[key]

        data = self._api_get_json(cur, "avatars", params={"marketplace": "all", "n": "100", "search": avatar_name})
        if not isinstance(data, list):
            self._api_search_cache[key] = ("", "")
            return "", ""

        best_id = ""
        best_creator = ""
        wanted_name = avatar_name.casefold()

        for item in data:
            if not isinstance(item, dict):
                continue

            candidate_id = str(item.get("id") or "")
            if not candidate_id.startswith("avtr_"):
                continue

            candidate_name = str(item.get("name") or "")
            candidate_creator = str(item.get("authorId") or "")
            if not candidate_name or candidate_name.casefold() != wanted_name:
                continue
            if owner_id and candidate_creator != owner_id:
                continue
            best_id = candidate_id
            best_creator = candidate_creator
            break

        if best_id:
            self._api_search_cache[key] = (best_id, best_creator)
            return best_id, best_creator

        self._api_search_cache[key] = ("", "")
        return "", ""

    def _avatar_id_from_history(self, cur: sqlite3.Cursor, user_id: str) -> str:
        table = self._history_table_name_for_user(user_id)
        if not table:
            return ""
        try:
            rows = cur.execute(
                f"SELECT avatar_id FROM [{table}] ORDER BY time DESC, created_at DESC LIMIT 80"
            ).fetchall()
        except sqlite3.Error:
            return ""
        for (avatar_id,) in rows:
            aid = str(avatar_id or "").strip()
            if aid.startswith("avtr_"):
                return aid
        return ""

    def _discover_feed_tables(self, cur: sqlite3.Cursor) -> List[str]:
        if self._feed_tables_cache is not None:
            return self._feed_tables_cache

        rows = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'usr%_feed_avatar'").fetchall()
        self._feed_tables_cache = sorted(str(row[0]) for row in rows)
        return self._feed_tables_cache

    @staticmethod
    def _max_id_for_table(cur: sqlite3.Cursor, table: str) -> int:
        try:
            row = cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM [{table}]").fetchone()
            if not row:
                return 0
            return int(row[0] or 0)
        except (sqlite3.Error, TypeError, ValueError):
            return 0

    @staticmethod
    def _history_table_name_for_user(user_id: str) -> str:
        if not user_id or not user_id.startswith("usr_"):
            return ""
        raw = user_id[4:].replace("-", "").lower()
        if not raw:
            return ""
        return f"usr{raw}_avatar_history"

    def _guess_avatar_id(
        self,
        cur: sqlite3.Cursor,
        user_id: str = "",
        avatar_name: str = "",
        owner_id: str = "",
        image_url: str = "",
        thumbnail_url: str = "",
    ) -> str:
        if image_url or thumbnail_url:
            try:
                row = None
                if image_url:
                    row = cur.execute(
                        "SELECT id FROM [cache_avatar] WHERE image_url = ? ORDER BY updated_at DESC LIMIT 1",
                        (image_url,),
                    ).fetchone()
                if row is None and thumbnail_url:
                    row = cur.execute(
                        "SELECT id FROM [cache_avatar] WHERE thumbnail_image_url = ? ORDER BY updated_at DESC LIMIT 1",
                        (thumbnail_url,),
                    ).fetchone()
                avatar_id = str(row[0] or "").strip() if row else ""
                if avatar_id.startswith("avtr_"):
                    return avatar_id
            except sqlite3.Error:
                pass

        if avatar_name:
            try:
                if owner_id:
                    row = cur.execute(
                        "SELECT id FROM [cache_avatar] WHERE name = ? AND author_id = ? ORDER BY updated_at DESC LIMIT 1",
                        (avatar_name, owner_id),
                    ).fetchone()
                else:
                    row = cur.execute(
                        "SELECT id FROM [cache_avatar] WHERE name = ? ORDER BY updated_at DESC LIMIT 1",
                        (avatar_name,),
                    ).fetchone()
                avatar_id = str(row[0] or "").strip() if row else ""
                if avatar_id.startswith("avtr_"):
                    return avatar_id
            except sqlite3.Error:
                pass

            api_avatar_id, _ = self._api_search_avatar(cur, avatar_name=avatar_name, owner_id=owner_id)
            if api_avatar_id:
                return api_avatar_id

            website_avatar_id = self._search_avatar_id_from_websites(avatar_name=avatar_name, owner_id=owner_id)
            if website_avatar_id.startswith("avtr_"):
                return website_avatar_id

        if user_id:
            avatar_id = self._avatar_id_from_history(cur, user_id=user_id)
            if avatar_id.startswith("avtr_"):
                return avatar_id

        return ""

    def _guess_avatar_creator(
        self,
        cur: sqlite3.Cursor,
        avatar_id: str = "",
        avatar_name: str = "",
        image_url: str = "",
        thumbnail_url: str = "",
        owner_hint: str = "",
    ) -> str:
        if avatar_id and avatar_id.startswith("avtr_"):
            try:
                row = cur.execute(
                    "SELECT author_id FROM [cache_avatar] WHERE id = ? ORDER BY updated_at DESC LIMIT 1",
                    (avatar_id,),
                ).fetchone()
                creator_id = str(row[0] or "").strip() if row else ""
                if creator_id.startswith("usr_"):
                    return creator_id
            except sqlite3.Error:
                pass

            api_creator, _, _, _ = self._api_avatar_meta(cur, avatar_id)
            if api_creator.startswith("usr_"):
                return api_creator

        if owner_hint and owner_hint.startswith("usr_"):
            return owner_hint

        if avatar_name:
            try:
                row = cur.execute(
                    "SELECT author_id FROM [cache_avatar] WHERE name = ? ORDER BY updated_at DESC LIMIT 1",
                    (avatar_name,),
                ).fetchone()
                creator_id = str(row[0] or "").strip() if row else ""
                if creator_id.startswith("usr_"):
                    return creator_id
            except sqlite3.Error:
                pass

            _, api_creator = self._api_search_avatar(cur, avatar_name=avatar_name, owner_id="")
            if api_creator.startswith("usr_"):
                return api_creator

        return ""

    def resolve_avatar_id(
        self,
        user_id: str = "",
        avatar_name: str = "",
        owner_id: str = "",
        image_url: str = "",
        thumbnail_url: str = "",
        event_created_at: str = "",
    ) -> str:
        if not self.db_path.exists():
            return ""

        cur = self._get_cursor()
        if cur is None:
            return ""

        try:
            return self._guess_avatar_id(
                cur,
                user_id=user_id,
                avatar_name=avatar_name,
                owner_id=owner_id,
                image_url=image_url,
                thumbnail_url=thumbnail_url,
            )
        except sqlite3.Error:
            self._reset_read_connection()
            return ""

    def resolve_avatar_creator(
        self,
        avatar_id: str = "",
        avatar_name: str = "",
        image_url: str = "",
        thumbnail_url: str = "",
        owner_hint: str = "",
    ) -> str:
        if not self.db_path.exists():
            return owner_hint or ""

        cur = self._get_cursor()
        if cur is None:
            return owner_hint or ""

        try:
            guessed = self._guess_avatar_creator(
                cur,
                avatar_id=avatar_id,
                avatar_name=avatar_name,
                image_url=image_url,
                thumbnail_url=thumbnail_url,
                owner_hint=owner_hint,
            )
            return guessed or (owner_hint or "")
        except sqlite3.Error:
            self._reset_read_connection()
            return owner_hint or ""

    def resolve_user_platform(self, user_id: str = "") -> str:
        if not user_id or not user_id.startswith("usr_"):
            return "Unknown"
        if not self.db_path.exists():
            return "Unknown"

        now = time.time()
        cached = self._api_user_platform_cache.get(user_id)
        if cached and (now - float(cached[1])) < self._api_user_platform_cache_ttl_seconds:
            return str(cached[0] or "Unknown")

        cur = self._get_cursor()
        if cur is None:
            return str(cached[0] or "Unknown") if cached else "Unknown"

        try:
            data = self._api_get_json(cur, f"users/{user_id}")
            if isinstance(data, dict):
                label = self._normalize_user_platform(
                    str(data.get("last_platform") or ""),
                    str(data.get("platform") or ""),
                )
                self._api_user_platform_cache[user_id] = (label, now)
                return label
        except sqlite3.Error:
            self._reset_read_connection()
            pass

        if cached:
            return str(cached[0] or "Unknown")
        return "Unknown"

    def resolve_avatar_event_meta(
        self,
        user_id: str = "",
        display_name: str = "",
        avatar_name: str = "",
        event_created_at: str = "",
    ) -> Tuple[str, str]:
        if not self.db_path.exists() or not avatar_name:
            return "", ""

        cur = self._get_cursor()
        if cur is None:
            return "", ""

        try:
            best: Optional[Tuple[int, str, str, str, str]] = None

            for table in self._discover_feed_tables(cur):
                row = None
                if user_id:
                    row = cur.execute(
                        f"""
                        SELECT id, created_at, owner_id, current_avatar_image_url, current_avatar_thumbnail_image_url
                        FROM [{table}]
                        WHERE user_id = ? AND avatar_name = ?
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (user_id, avatar_name),
                    ).fetchone()

                if row is None and display_name:
                    row = cur.execute(
                        f"""
                        SELECT id, created_at, owner_id, current_avatar_image_url, current_avatar_thumbnail_image_url
                        FROM [{table}]
                        WHERE display_name = ? AND avatar_name = ?
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (display_name, avatar_name),
                    ).fetchone()

                if row is None:
                    continue

                rid = int(row[0] or 0)
                created_at = str(row[1] or "")
                owner_hint = str(row[2] or "")
                image_url = str(row[3] or "")
                thumbnail_url = str(row[4] or "")
                if best is None or rid > best[0]:
                    best = (rid, created_at, owner_hint, image_url, thumbnail_url)

            if best is None:
                return "", ""

            _, created_at, owner_hint, image_url, thumbnail_url = best
            resolved_avatar_id = self._guess_avatar_id(
                cur,
                user_id=user_id,
                avatar_name=avatar_name,
                owner_id=owner_hint,
                image_url=image_url,
                thumbnail_url=thumbnail_url,
            )
            resolved_creator = self._guess_avatar_creator(
                cur,
                avatar_id=resolved_avatar_id,
                avatar_name=avatar_name,
                image_url=image_url,
                thumbnail_url=thumbnail_url,
                owner_hint=owner_hint,
            )
            return resolved_creator, resolved_avatar_id
        except sqlite3.Error:
            self._reset_read_connection()
            return "", ""

    def _to_change(self, table: str, row: Tuple[object, ...], cur: Optional[sqlite3.Cursor] = None) -> AvatarChange:
        image_url = str(row[6] or "") if len(row) > 6 else ""
        thumbnail_url = str(row[7] or "") if len(row) > 7 else ""

        event = AvatarChange(
            table_name=table,
            row_id=int(row[0] or 0),
            created_at=str(row[1] or ""),
            user_id=str(row[2] or ""),
            display_name=str(row[3] or ""),
            avatar_name=str(row[4] or ""),
            owner_id=str(row[5] or ""),
        )
        if cur is not None and event.avatar_name:
            event.avatar_id = self._guess_avatar_id(
                cur,
                user_id=event.user_id,
                avatar_name=event.avatar_name,
                owner_id=event.owner_id,
                image_url=image_url,
                thumbnail_url=thumbnail_url,
            )
            event.owner_id = self._guess_avatar_creator(
                cur,
                avatar_id=event.avatar_id,
                avatar_name=event.avatar_name,
                image_url=image_url,
                thumbnail_url=thumbnail_url,
                owner_hint=event.owner_id,
            )
        return event

    @staticmethod
    def _change_sort_key(change: AvatarChange) -> Tuple[str, int]:
        return (change.created_at, change.row_id)

    def initialize_positions(self):
        if not self.db_path.exists():
            return

        cur = self._get_cursor()
        if cur is None:
            return

        try:
            for table in self._discover_feed_tables(cur):
                self.last_seen_id_by_table[table] = self._max_id_for_table(cur, table)
        except sqlite3.Error:
            self._reset_read_connection()

    def get_latest_avatar_for_player(self, user_id: str = "", display_name: str = "") -> Optional[AvatarChange]:
        if not self.db_path.exists():
            return None

        latest: Optional[AvatarChange] = None

        cur = self._get_cursor()
        if cur is None:
            return None

        try:
            for table in self._discover_feed_tables(cur):
                row = None
                if user_id:
                    row = cur.execute(
                        f"""
                        SELECT id, created_at, user_id, display_name, avatar_name, owner_id, current_avatar_image_url, current_avatar_thumbnail_image_url
                        FROM [{table}]
                        WHERE user_id = ? AND avatar_name IS NOT NULL AND avatar_name != ''
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (user_id,),
                    ).fetchone()

                if row is None and display_name:
                    row = cur.execute(
                        f"""
                        SELECT id, created_at, user_id, display_name, avatar_name, owner_id, current_avatar_image_url, current_avatar_thumbnail_image_url
                        FROM [{table}]
                        WHERE display_name = ? AND avatar_name IS NOT NULL AND avatar_name != ''
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (display_name,),
                    ).fetchone()

                if row is None:
                    continue

                candidate = self._to_change(table, row, cur)
                if not latest or self._change_sort_key(candidate) > self._change_sort_key(latest):
                    latest = candidate
        except sqlite3.Error:
            self._reset_read_connection()
            return None

        return latest

    def poll(self) -> List[AvatarChange]:
        events: List[AvatarChange] = []
        if not self.db_path.exists():
            return events

        cur = self._get_cursor()
        if cur is None:
            return events

        try:
            feed_tables = self._discover_feed_tables(cur)
            active_tables = set(feed_tables)

            for table in feed_tables:
                if table not in self.last_seen_id_by_table:
                    self.last_seen_id_by_table[table] = self._max_id_for_table(cur, table)

            for table in feed_tables:
                last_id = self.last_seen_id_by_table.get(table, 0)
                rows = cur.execute(
                    f"""
                    SELECT id, created_at, user_id, display_name, avatar_name, owner_id, current_avatar_image_url, current_avatar_thumbnail_image_url
                    FROM [{table}]
                    WHERE id > ?
                    ORDER BY id ASC
                    """,
                    (last_id,),
                ).fetchall()

                for row in rows:
                    event = self._to_change(table, row, cur)
                    events.append(event)
                    if event.row_id > self.last_seen_id_by_table[table]:
                        self.last_seen_id_by_table[table] = event.row_id

            for known_table in list(self.last_seen_id_by_table.keys()):
                if known_table not in active_tables:
                    self.last_seen_id_by_table.pop(known_table, None)
        except sqlite3.Error:
            self._reset_read_connection()
            return []

        return events
