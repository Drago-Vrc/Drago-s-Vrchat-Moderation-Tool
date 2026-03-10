from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import requests

from .config import env_flag


class Discord:
    def __init__(self, webhook_url: str, printer: Callable[..., object] = print):
        self.url = (webhook_url or "").strip()
        self.enabled = bool(self.url)
        self._print = printer

    def _post_json(self, payload: Dict[str, object]) -> bool:
        if not self.enabled:
            return False

        allow_insecure_ssl = env_flag("KRYZLN_ALLOW_INSECURE_SSL")
        try:
            resp = requests.post(self.url, json=payload, timeout=8)
        except requests.exceptions.SSLError as exc:
            if not allow_insecure_ssl:
                self._print(
                    "[!] Webhook SSL error. Update system certs or set "
                    "KRYZLN_ALLOW_INSECURE_SSL=1 to allow insecure retry."
                )
                self._print(f"[!] SSL details: {exc}")
                return False
            self._print("[!] Webhook SSL error. Retrying with insecure SSL because KRYZLN_ALLOW_INSECURE_SSL=1.")
            try:
                resp = requests.post(self.url, json=payload, timeout=8, verify=False)
            except requests.RequestException as retry_exc:
                self._print(f"[!] Webhook request failed: {retry_exc}")
                return False
        except requests.RequestException as exc:
            self._print(f"[!] Webhook request failed: {exc}")
            return False

        if 200 <= resp.status_code < 300:
            return True

        body = (resp.text or "").replace("\n", " ").strip()
        if len(body) > 220:
            body = body[:220] + "..."
        self._print(f"[!] Webhook HTTP {resp.status_code}: {body or '(empty response)'}")
        return False

    def send_embed(
        self,
        title: str,
        description: str,
        color: int,
        fields: Optional[List[Dict[str, object]]] = None,
    ) -> bool:
        if not self.enabled:
            return False

        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": color,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "footer": {"text": "Drago's Moderation Tool"},
                }
            ]
        }
        if fields:
            payload["embeds"][0]["fields"] = fields

        ok = self._post_json(payload)
        if ok:
            return True

        self._print("[!] Embed send failed, retrying webhook as plain content.")
        plain = f"{title}: {description or ''}".strip(": ")
        plain = plain.replace("**", "").replace("`", "")
        return self._post_json({"content": plain[:1800]})

    def player_join(self, username: str, user_id: str = ""):
        fields = []
        if user_id:
            fields.append({"name": "User ID", "value": f"`{user_id}`", "inline": False})
        self.send_embed("Player Joined", f"**{username}** joined the world", 0x00AA00, fields)

    def player_leave(self, username: str, user_id: str = ""):
        fields = []
        if user_id:
            fields.append({"name": "User ID", "value": f"`{user_id}`", "inline": False})
        self.send_embed("Player Left", f"**{username}** left the world", 0xCC0000, fields)

    def world_change(self, world_name: str):
        self.send_embed("World Changed", world_name, 0x1F6FEB)

    def avatar_change(
        self,
        username: str,
        old_avatar_name: str,
        new_avatar_name: str,
        user_id: str = "",
        old_creator_id: str = "",
        new_creator_id: str = "",
        old_avatar_id: str = "",
        new_avatar_id: str = "",
        created_at: str = "",
    ):
        fields = []
        if user_id:
            fields.append({"name": "User ID", "value": f"`{user_id}`", "inline": False})

        fields.append({"name": "Old Creator ID", "value": f"`{old_creator_id or 'unknown'}`", "inline": False})
        fields.append({"name": "New Creator ID", "value": f"`{new_creator_id or 'unknown'}`", "inline": False})
        fields.append({"name": "Old Avatar ID", "value": f"`{old_avatar_id or 'unknown'}`", "inline": False})
        fields.append({"name": "New Avatar ID", "value": f"`{new_avatar_id or 'unknown'}`", "inline": False})

        if created_at:
            fields.append({"name": "Event Time", "value": created_at, "inline": False})

        self.send_embed(
            "Avatar Changed",
            f"**{username}**: **{old_avatar_name or 'unknown'}** -> **{new_avatar_name}**",
            0x8A2BE2,
            fields,
        )

    def stability(self, level: str, reason: str, fields: Optional[List[Dict[str, object]]] = None):
        level_up = (level or "").upper()
        color = {"GREEN": 0x00AA00, "YELLOW": 0xE6A700, "RED": 0xCC0000}.get(level_up, 0x1F6FEB)
        self.send_embed(f"STABILITY: {level_up}", reason, color, fields)
