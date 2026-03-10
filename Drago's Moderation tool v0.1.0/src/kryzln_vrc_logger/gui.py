import ctypes
import math
import os
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from queue import Empty, Full, Queue
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple

from .config import Config
from .engine import VRCLogger
from .printing import add_print_listener, remove_print_listener, safe_print
from .webhook import Discord


class ModerationToolGUI:
    BG = "#060B18"
    PANEL = "#0E1A30"
    PANEL_ALT = "#132542"
    TEXT = "#ECF2FF"
    MUTED = "#9EB5D1"
    ACCENT = "#35D2FF"
    GREEN = "#47D17B"
    YELLOW = "#F4BE4D"
    RED = "#FF6767"

    def __init__(self, logger_factory: Optional[Callable[[], VRCLogger]] = None):
        Config.init()
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("dragos.moderation.tool")
        except (AttributeError, OSError):
            pass

        self.root = tk.Tk()
        self.root.title("Drago's Moderation Tool")
        self.root.geometry("1360x840")
        self.root.minsize(1080, 680)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._set_window_icon()

        self.ui_queue: Queue[Tuple[str, object]] = Queue(maxsize=1200)
        self.worker_stop = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.logger_factory = logger_factory or VRCLogger
        self.logger: Optional[VRCLogger] = None
        self.status_running = False
        self.stability_color = self.GREEN
        self.risk_level = "GREEN"
        self.stability_gif_level = "GREEN"
        self.stability_gif_frame_index = 0
        self.stability_gif_frames: Dict[str, List[tk.PhotoImage]] = {}
        self.stability_gif_delays: Dict[str, List[int]] = {}
        self.stability_gif_after_id: Optional[str] = None
        self.stability_gif_label: Optional[tk.Label] = None
        self.risk_header_label: Optional[tk.Label] = None
        self.status_dot_id: Optional[int] = None
        self.status_halo_id: Optional[int] = None
        self.status_text_id: Optional[int] = None
        self._pulse_phase = 0.0
        self._init_fonts()
        self.clear_icon_image = self._load_clear_button_icon()
        self.discord_badge_image = self._load_discord_badge_icon()
        self.stability_gif_frames, self.stability_gif_delays = self._load_stability_gif_assets(target_px=88)
        self.brand_logo_image, self.brand_wordmark_image = self._load_brand_logo_assets()
        self.discord_handle = os.getenv("KRYZLN_DISCORD_HANDLE", "").strip()

        self.running_var = tk.StringVar(value="OFFLINE")
        self.world_var = tk.StringVar(value="(none)")
        self.players_var = tk.StringVar(value="0")
        self.stability_var = tk.StringVar(value="GREEN")
        self.reason_var = tk.StringVar(value="stable")
        self.avatar_var = tk.StringVar(value="0")
        self.rapid_var = tk.StringVar(value="0")
        self.mass_leave_var = tk.StringVar(value="0")
        self.webhook_var = tk.StringVar(value=Config.DISCORD_WEBHOOK)

        self._build_styles()
        self._build_layout()

        add_print_listener(self._queue_log_message)
        self._append_log("Starting tool...\n", forced_tag="system")
        self._start_worker()

        self.root.after(80, self._process_queue)
        self.root.after(120, self._animate_status_dot)
        self.root.after(90, self._animate_stability_gif)

    def _set_window_icon(self):
        candidates: List[Path] = []

        env_icon = os.environ.get("KRYZLN_APP_ICON", "").strip()
        if env_icon:
            candidates.append(Path(env_icon))

        app_dir = Config.OUTPUT_DIR
        candidates.append(app_dir / "assets" / "icons" / "favicon.ico")

        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass) / "assets" / "icons" / "favicon.ico")

        for icon_path in candidates:
            try:
                if icon_path.exists():
                    self.root.iconbitmap(default=str(icon_path))
                    return
            except (tk.TclError, OSError):
                continue

    def _header_icon_candidates(self) -> List[Path]:
        candidates: List[Path] = []

        env_icon = os.environ.get("KRYZLN_HEADER_ICON", "").strip()
        if env_icon:
            candidates.append(Path(env_icon))

        app_dir = Config.OUTPUT_DIR
        candidates.append(app_dir / "assets" / "icons" / "favicon.ico")

        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass) / "assets" / "icons" / "favicon.ico")

        return candidates

    def _header_wordmark_candidates(self) -> List[Path]:
        candidates: List[Path] = []

        env_wordmark = os.environ.get("KRYZLN_HEADER_WORDMARK", "").strip()
        if env_wordmark:
            candidates.append(Path(env_wordmark))

        names = [
            "drago_wordmark.png",
            "wordmark.png",
            "drago_text.png",
            "drago-logo-text.png",
        ]
        app_dir = Config.OUTPUT_DIR
        for name in names:
            candidates.append(app_dir / "assets" / "icons" / name)

        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            for name in names:
                candidates.append(Path(meipass) / "assets" / "icons" / name)

        return candidates

    @staticmethod
    def _find_non_bg_bbox(pil_image, tolerance: int = 14) -> Optional[Tuple[int, int, int, int]]:
        rgba = pil_image.convert("RGBA")
        w, h = rgba.size
        if w <= 0 or h <= 0:
            return None

        px = rgba.load()
        corners = [
            px[0, 0],
            px[max(0, w - 1), 0],
            px[0, max(0, h - 1)],
            px[max(0, w - 1), max(0, h - 1)],
        ]
        bg_r = int(sum(c[0] for c in corners) / len(corners))
        bg_g = int(sum(c[1] for c in corners) / len(corners))
        bg_b = int(sum(c[2] for c in corners) / len(corners))

        left, top = w, h
        right = -1
        bottom = -1
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a <= 6:
                    continue
                if (
                    abs(r - bg_r) <= tolerance
                    and abs(g - bg_g) <= tolerance
                    and abs(b - bg_b) <= tolerance
                ):
                    continue
                if x < left:
                    left = x
                if y < top:
                    top = y
                if x > right:
                    right = x
                if y > bottom:
                    bottom = y

        if right < left or bottom < top:
            alpha_bbox = rgba.getchannel("A").getbbox()
            return alpha_bbox
        return (left, top, right + 1, bottom + 1)

    def _load_clear_button_icon(self) -> Optional[tk.PhotoImage]:
        candidates: List[Path] = []

        env_icon = os.environ.get("KRYZLN_BROOM_ICON", "").strip()
        if env_icon:
            candidates.append(Path(env_icon))

        app_dir = Config.OUTPUT_DIR
        candidates.append(app_dir / "assets" / "icons" / "broom.png")
        candidates.append(app_dir / "assets" / "icons" / "broom.ico")

        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass) / "assets" / "icons" / "broom.png")
            candidates.append(Path(meipass) / "assets" / "icons" / "broom.ico")

        try:
            from PIL import Image, ImageTk  # type: ignore
        except ImportError:
            return None

        for icon_path in candidates:
            try:
                if not icon_path.exists():
                    continue

                pil = Image.open(icon_path).convert("RGBA")
                if hasattr(Image, "Resampling"):
                    resample = Image.Resampling.LANCZOS
                else:
                    resample = Image.LANCZOS
                pil = pil.resize((14, 14), resample)
                image = ImageTk.PhotoImage(pil)
                safe_print(f"[+] Loaded clear-feed icon: {icon_path}")
                return image
            except (OSError, RuntimeError, tk.TclError, ValueError):
                continue
        return None

    def _discord_badge_icon_candidates(self) -> List[Path]:
        candidates: List[Path] = []

        env_icon = os.environ.get("KRYZLN_DISCORD_ICON", "").strip()
        if env_icon:
            candidates.append(Path(env_icon))

        app_dir = Config.OUTPUT_DIR

        names = [
            "discord-logo.png",
            "discord-logo.jpg",
        ]

        meipass = getattr(sys, "_MEIPASS", "")
        for name in names:
            candidates.append(app_dir / "assets" / "icons" / name)
            if meipass:
                candidates.append(Path(meipass) / "assets" / "icons" / name)

        return candidates

    def _load_discord_badge_icon(self) -> Optional[tk.PhotoImage]:
        try:
            from PIL import Image, ImageTk  # type: ignore
        except ImportError:
            return None

        target_px = 26
        for icon_path in self._discord_badge_icon_candidates():
            try:
                if not icon_path.exists():
                    continue

                pil = Image.open(icon_path).convert("RGBA")
                crop_bbox = self._find_non_bg_bbox(pil, tolerance=20)
                if crop_bbox:
                    pil = pil.crop(crop_bbox)

                alpha_bbox = pil.getchannel("A").getbbox()
                if alpha_bbox:
                    pil = pil.crop(alpha_bbox)

                if hasattr(Image, "Resampling"):
                    resample = Image.Resampling.LANCZOS
                else:
                    resample = Image.LANCZOS
                pil = pil.resize((target_px, target_px), resample)
                image = ImageTk.PhotoImage(pil)
                safe_print(f"[+] Loaded Discord footer icon: {icon_path}")
                return image
            except (OSError, RuntimeError, tk.TclError, ValueError):
                continue
        return None

    def _stability_gif_candidates(self, level: str) -> List[Path]:
        level_up = (level or "").upper()
        env_key = f"KRYZLN_STABILITY_GIF_{level_up}"
        candidates: List[Path] = []

        env_path = os.environ.get(env_key, "").strip()
        if env_path:
            candidates.append(Path(env_path))

        names_by_level: Dict[str, List[str]] = {
            "GREEN": ["stability_green.gif"],
            "YELLOW": ["stability_yellow.gif"],
            "RED": ["stability_red.gif"],
        }

        names = names_by_level.get(level_up, [])
        app_dir = Config.OUTPUT_DIR
        for name in names:
            candidates.append(app_dir / "assets" / "gifs" / name)

        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            for name in names:
                candidates.append(Path(meipass) / "assets" / "gifs" / name)

        return candidates

    @staticmethod
    def _make_dark_background_transparent(pil_image, threshold: int = 8):
        rgba = pil_image.convert("RGBA")
        pixels = list(rgba.getdata())
        processed = []
        for r, g, b, a in pixels:
            if a > 0 and r <= threshold and g <= threshold and b <= threshold:
                processed.append((r, g, b, 0))
            else:
                processed.append((r, g, b, a))
        rgba.putdata(processed)
        return rgba

    def _load_stability_gif_assets(self, target_px: int = 88) -> Tuple[Dict[str, List[tk.PhotoImage]], Dict[str, List[int]]]:
        frames_map: Dict[str, List[tk.PhotoImage]] = {}
        delays_map: Dict[str, List[int]] = {}

        try:
            from PIL import Image, ImageTk  # type: ignore
        except ImportError:
            return frames_map, delays_map

        if hasattr(Image, "Resampling"):
            resample = Image.Resampling.LANCZOS
        else:
            resample = Image.LANCZOS

        for level in ("GREEN", "YELLOW", "RED"):
            for gif_path in self._stability_gif_candidates(level):
                try:
                    if not gif_path.exists():
                        continue

                    gif_frames: List[tk.PhotoImage] = []
                    gif_delays: List[int] = []

                    with Image.open(gif_path) as pil:
                        frame_count = max(1, int(getattr(pil, "n_frames", 1)))
                        for idx in range(frame_count):
                            pil.seek(idx)
                            frame = pil.convert("RGBA")
                            max_dim = max(frame.width, frame.height)
                            if max_dim <= 0:
                                continue
                            scale = float(target_px) / float(max_dim)
                            render_size = (
                                max(1, int(frame.width * scale)),
                                max(1, int(frame.height * scale)),
                            )
                            frame = frame.resize(render_size, resample)
                            frame = self._make_dark_background_transparent(frame, threshold=8)
                            gif_frames.append(ImageTk.PhotoImage(frame))

                            duration = int(pil.info.get("duration", 70) or 70)
                            gif_delays.append(max(35, min(220, duration)))

                    if gif_frames:
                        frames_map[level] = gif_frames
                        delays_map[level] = gif_delays if gif_delays else [70] * len(gif_frames)
                        safe_print(f"[+] Loaded {level} stability GIF: {gif_path}")
                        break
                except (OSError, RuntimeError, tk.TclError, ValueError):
                    continue

        return frames_map, delays_map

    def _load_brand_logo_assets(self) -> Tuple[Optional[tk.PhotoImage], Optional[tk.PhotoImage]]:
        icon_target_px = 90
        wordmark_target_h = 36

        try:
            from PIL import Image, ImageTk  # type: ignore
        except ImportError:
            return None, None

        icon_img: Optional[tk.PhotoImage] = None
        for icon_path in self._header_icon_candidates():
            try:
                if not icon_path.exists():
                    continue
                pil = Image.open(icon_path).convert("RGBA")
                alpha_bbox = pil.getchannel("A").getbbox()
                if alpha_bbox:
                    pil = pil.crop(alpha_bbox)

                max_dim = max(1, max(pil.width, pil.height))
                scale = icon_target_px / float(max_dim)
                icon_size = (
                    max(1, int(pil.width * scale)),
                    max(1, int(pil.height * scale)),
                )
                if hasattr(Image, "Resampling"):
                    resample = Image.Resampling.LANCZOS
                else:
                    resample = Image.LANCZOS
                icon_img = ImageTk.PhotoImage(pil.resize(icon_size, resample))
                safe_print(f"[+] Loaded header icon: {icon_path}")
                break
            except (OSError, RuntimeError, tk.TclError, ValueError):
                continue

        wordmark_img: Optional[tk.PhotoImage] = None
        for wordmark_path in self._header_wordmark_candidates():
            try:
                if not wordmark_path.exists():
                    continue
                pil = Image.open(wordmark_path).convert("RGBA")
                bbox = self._find_non_bg_bbox(pil, tolerance=15)
                if bbox:
                    pil = pil.crop(bbox)

                if pil.height <= 0 or pil.width <= 0:
                    continue
                word_scale = wordmark_target_h / float(pil.height)
                word_size = (
                    max(1, int(pil.width * word_scale)),
                    max(1, int(pil.height * word_scale)),
                )
                if hasattr(Image, "Resampling"):
                    resample = Image.Resampling.LANCZOS
                else:
                    resample = Image.LANCZOS
                wordmark_img = ImageTk.PhotoImage(pil.resize(word_size, resample))
                safe_print(f"[+] Loaded header wordmark: {wordmark_path}")
                break
            except (OSError, RuntimeError, tk.TclError, ValueError):
                continue

        return icon_img, wordmark_img

    def _draw_discord_badge_icon(self):
        if not hasattr(self, "discord_icon_canvas"):
            return

        canvas = self.discord_icon_canvas
        canvas.delete("all")
        cx = max(1, int(canvas.winfo_reqwidth() // 2))
        cy = max(1, int(canvas.winfo_reqheight() // 2))
        if self.discord_badge_image is not None:
            canvas.create_image(cx, cy, image=self.discord_badge_image, anchor="center")
            return
        canvas.create_oval(1, 1, 27, 27, fill="#5865F2", outline="")
        canvas.create_rectangle(6, 10, 22, 19, fill="#5865F2", outline="")
        canvas.create_oval(10, 12, 12, 14, fill="#FFFFFF", outline="")
        canvas.create_oval(16, 12, 18, 14, fill="#FFFFFF", outline="")
        canvas.create_arc(9, 13, 19, 21, start=205, extent=130, style="arc", outline="#FFFFFF", width=1.5)

    def _copy_discord_handle(self, _event=None):
        if not self.discord_handle:
            safe_print("[!] No Discord handle configured.")
            return

        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.discord_handle)
            self.root.update_idletasks()
            safe_print(f"[+] Copied Discord username: {self.discord_handle}")
        except tk.TclError as exc:
            safe_print(f"[!] Failed to copy Discord username: {exc}")

    def _pick_font_family(self, preferred: List[str], fallback: str = "Segoe UI") -> str:
        try:
            available = {name.casefold(): name for name in tkfont.families(self.root)}
            for candidate in preferred:
                exact = available.get(candidate.casefold())
                if exact:
                    return exact
            for candidate in preferred:
                needle = candidate.casefold()
                for key, value in available.items():
                    if needle in key:
                        return value
        except tk.TclError:
            pass
        return fallback

    def _init_fonts(self):
        title_family = self._pick_font_family(
            ["Orbitron SemiBold", "Orbitron", "Rajdhani SemiBold", "Rajdhani", "Bahnschrift SemiBold", "Bahnschrift"]
        )
        panel_family = self._pick_font_family(
            ["Rajdhani Medium", "Rajdhani", "Bahnschrift SemiBold", "Bahnschrift", "Segoe UI"]
        )
        mono_family = self._pick_font_family(
            ["JetBrains Mono", "JetBrainsMono Nerd Font", "JetBrainsMono NF", "Cascadia Code", "Consolas"]
        )

        self.font_title = (title_family, 25, "bold")
        self.font_panel_title = (panel_family, 12, "bold")
        self.font_panel = (panel_family, 11)
        self.font_panel_button = (panel_family, 10, "bold")
        self.font_panel_value = (panel_family, 18, "bold")
        self.font_log = (mono_family, 10)
        self.font_mono_small = (mono_family, 10)
        self.font_mono_tiny = (mono_family, 11)

    @staticmethod
    def _attach_hover_glow(button: ttk.Button, normal_style: str, hover_style: str):
        def _is_disabled() -> bool:
            try:
                return "disabled" in button.state()
            except tk.TclError:
                return False

        def on_enter(_event=None):
            if _is_disabled():
                return
            try:
                button.configure(style=hover_style)
            except tk.TclError:
                pass

        def on_leave(_event=None):
            try:
                button.configure(style=normal_style)
            except tk.TclError:
                pass

        button.bind("<Enter>", on_enter, add="+")
        button.bind("<Leave>", on_leave, add="+")
        button.bind("<FocusIn>", on_enter, add="+")
        button.bind("<FocusOut>", on_leave, add="+")

    def _build_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Card.TFrame", background=self.PANEL)
        style.configure(
            "Dashboard.Treeview",
            background=self.PANEL_ALT,
            foreground=self.TEXT,
            fieldbackground=self.PANEL_ALT,
            bordercolor=self.PANEL_ALT,
            rowheight=26,
            font=self.font_panel,
        )
        style.map(
            "Dashboard.Treeview",
            background=[("selected", "#1D3E68")],
            foreground=[("selected", "#ECF2FF")],
        )
        style.configure(
            "Dashboard.Treeview.Heading",
            background="#1B3358",
            foreground="#DDF3FF",
            font=self.font_panel_button,
            relief="flat",
        )
        style.configure(
            "Accent.TButton",
            background=self.ACCENT,
            foreground="#061423",
            font=self.font_panel_button,
            padding=(12, 6),
            anchor="center",
            relief="flat",
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#66E3FF"), ("disabled", "#2E4B64")],
            foreground=[("disabled", "#8CA4BF")],
        )
        style.configure(
            "AccentGlow.TButton",
            background="#7AEBFF",
            foreground="#041320",
            font=self.font_panel_button,
            padding=(12, 6),
            anchor="center",
            relief="flat",
        )
        style.configure(
            "Warn.TButton",
            background="#FF8D5C",
            foreground="#210A00",
            font=self.font_panel_button,
            padding=(12, 6),
            anchor="center",
            relief="flat",
        )
        style.map(
            "Warn.TButton",
            background=[("active", "#FFB184"), ("disabled", "#5A3D31")],
            foreground=[("disabled", "#B9978E")],
        )
        style.configure(
            "WarnGlow.TButton",
            background="#FFC295",
            foreground="#2A0E00",
            font=self.font_panel_button,
            padding=(12, 6),
            anchor="center",
            relief="flat",
        )
        style.configure(
            "Subtle.TButton",
            background="#243B60",
            foreground="#D7E6FA",
            font=self.font_panel_button,
            padding=(12, 6),
            anchor="center",
            relief="flat",
        )
        style.map(
            "Subtle.TButton",
            background=[("active", "#355989"), ("disabled", "#26354A")],
            foreground=[("disabled", "#8193A8")],
        )
        style.configure(
            "SubtleGlow.TButton",
            background="#3C6397",
            foreground="#EAF4FF",
            font=self.font_panel_button,
            padding=(12, 6),
            anchor="center",
            relief="flat",
        )

    def _build_layout(self):
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(2, weight=0)
        self.root.grid_columnconfigure(0, weight=1)

        self.header_canvas = tk.Canvas(
            self.root,
            bg=self.BG,
            highlightthickness=0,
            height=172,
            bd=0,
        )
        self.header_canvas.grid(row=0, column=0, sticky="ew")
        self.header_canvas.bind("<Configure>", self._draw_header)

        content = tk.Frame(self.root, bg=self.BG)
        content.grid(row=1, column=0, sticky="nsew", padx=14, pady=(8, 10))
        content.grid_columnconfigure(0, weight=7, minsize=640)
        content.grid_columnconfigure(1, weight=5, minsize=320)
        content.grid_rowconfigure(1, weight=1)

        cards_frame = tk.Frame(content, bg=self.BG)
        cards_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        for col in range(6):
            cards_frame.grid_columnconfigure(col, weight=1)

        _, _, _ = self._create_metric_card(cards_frame, 0, "VRCHAT", self.running_var, "process state")
        _, _, _ = self._create_metric_card(cards_frame, 1, "PLAYERS", self.players_var, self.avatar_var)
        self.stability_card, self.stability_value_label, _ = self._create_stability_gif_card(
            cards_frame,
            2,
            "STABILITY",
            self.stability_var,
            self.reason_var,
        )
        _, _, _ = self._create_metric_card(cards_frame, 3, "RAPID SWITCH", self.rapid_var, "events in 8s")
        _, _, _ = self._create_metric_card(cards_frame, 4, "MASS LEAVES", self.mass_leave_var, "incident count")
        _, _, _ = self._create_metric_card(cards_frame, 5, "WORLD", self.world_var, "current")

        left_panel = ttk.Frame(content, style="Card.TFrame")
        left_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left_panel.grid_columnconfigure(0, weight=1)
        left_panel.grid_rowconfigure(1, weight=5)
        left_panel.grid_rowconfigure(3, weight=3)

        players_header = tk.Frame(left_panel, bg=self.PANEL)
        players_header.grid(row=0, column=0, sticky="ew")
        players_header.grid_columnconfigure(0, weight=1)

        tk.Label(
            players_header,
            text="ACTIVE PLAYERS",
            fg="#CBE3FF",
            bg=self.PANEL,
            font=self.font_panel_title,
            anchor="w",
            padx=12,
            pady=10,
        ).grid(row=0, column=0, sticky="w")
        self.copy_player_btn = ttk.Button(
            players_header,
            text="Copy Selected",
            style="Subtle.TButton",
            command=self._copy_selected_player,
        )
        self.copy_player_btn.grid(row=0, column=1, sticky="e", padx=(6, 10), pady=6)

        players_table_frame = tk.Frame(left_panel, bg=self.PANEL)
        players_table_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        players_table_frame.grid_columnconfigure(0, weight=1)
        players_table_frame.grid_rowconfigure(0, weight=1)
        players_table_frame.grid_rowconfigure(1, weight=0)

        self.players_tree = ttk.Treeview(
            players_table_frame,
            columns=("username", "user_id", "platform", "avatar", "avatar_id", "creator_id"),
            displaycolumns=("username", "user_id", "platform", "avatar", "avatar_id"),
            show="headings",
            style="Dashboard.Treeview",
            selectmode="browse",
        )
        self.players_tree.heading("username", text="Username")
        self.players_tree.heading("user_id", text="User ID")
        self.players_tree.heading("platform", text="Platform")
        self.players_tree.heading("avatar", text="Avatar")
        self.players_tree.heading("avatar_id", text="Avatar ID")
        self.players_tree.heading("creator_id", text="Creator ID")
        self.players_tree.column("username", width=150, minwidth=120, anchor="w", stretch=False)
        self.players_tree.column("user_id", width=210, minwidth=160, anchor="w", stretch=False)
        self.players_tree.column("platform", width=90, minwidth=78, anchor="center", stretch=False)
        self.players_tree.column("avatar", width=220, minwidth=160, anchor="w", stretch=False)
        self.players_tree.column("avatar_id", width=240, minwidth=170, anchor="w", stretch=False)
        self.players_tree.column("creator_id", width=210, minwidth=150, anchor="w", stretch=False)
        self.players_tree.grid(row=0, column=0, sticky="nsew")

        player_scroll_y = ttk.Scrollbar(players_table_frame, orient="vertical", command=self.players_tree.yview)
        player_scroll_y.grid(row=0, column=1, sticky="ns")
        player_scroll_x = ttk.Scrollbar(players_table_frame, orient="horizontal", command=self.players_tree.xview)
        player_scroll_x.grid(row=1, column=0, sticky="ew")
        self.players_tree.configure(yscrollcommand=player_scroll_y.set, xscrollcommand=player_scroll_x.set)

        self.risk_header_label = tk.Label(
            left_panel,
            text="RISK WATCH",
            fg="#CBE3FF",
            bg=self.PANEL,
            font=self.font_panel_title,
            anchor="w",
            padx=12,
            pady=6,
        )
        self.risk_header_label.grid(row=2, column=0, sticky="ew")

        risk_table_frame = tk.Frame(left_panel, bg=self.PANEL)
        risk_table_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        risk_table_frame.grid_columnconfigure(0, weight=1)
        risk_table_frame.grid_rowconfigure(0, weight=1)
        risk_table_frame.grid_rowconfigure(1, weight=0)

        self.risk_tree = ttk.Treeview(
            risk_table_frame,
            columns=("username", "joins", "switches", "crash"),
            show="headings",
            style="Dashboard.Treeview",
        )
        self.risk_tree.heading("username", text="User")
        self.risk_tree.heading("joins", text="Joins/10m")
        self.risk_tree.heading("switches", text="Switches/60s")
        self.risk_tree.heading("crash", text="Crash Corr")
        self.risk_tree.column("username", width=260, minwidth=140, anchor="w", stretch=True)
        self.risk_tree.column("joins", width=150, minwidth=90, anchor="center", stretch=True)
        self.risk_tree.column("switches", width=160, minwidth=100, anchor="center", stretch=True)
        self.risk_tree.column("crash", width=150, minwidth=90, anchor="center", stretch=True)
        self.risk_tree.grid(row=0, column=0, sticky="nsew")

        risk_scroll = ttk.Scrollbar(risk_table_frame, orient="vertical", command=self.risk_tree.yview)
        risk_scroll.grid(row=0, column=1, sticky="ns")
        risk_scroll_x = ttk.Scrollbar(risk_table_frame, orient="horizontal", command=self.risk_tree.xview)
        risk_scroll_x.grid(row=1, column=0, sticky="ew")
        self.risk_tree.configure(yscrollcommand=risk_scroll.set, xscrollcommand=risk_scroll_x.set)

        right_panel = ttk.Frame(content, style="Card.TFrame")
        right_panel.grid(row=1, column=1, sticky="nsew")
        right_panel.grid_rowconfigure(1, weight=1)
        right_panel.grid_rowconfigure(2, weight=0)
        right_panel.grid_columnconfigure(0, weight=1)

        controls = tk.Frame(right_panel, bg=self.PANEL)
        controls.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        controls.grid_columnconfigure(0, weight=1)

        tk.Label(
            controls,
            text="🟢 LIVE EVENT FEED",
            fg="#CBE3FF",
            bg=self.PANEL,
            font=self.font_panel_title,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        button_wrap = tk.Frame(controls, bg=self.PANEL)
        button_wrap.grid(row=1, column=0, sticky="ew", pady=(8, 2))
        for col in range(3):
            button_wrap.grid_columnconfigure(col, weight=1)

        self.start_btn = ttk.Button(
            button_wrap,
            text="▶ Start Scanner",
            style="Accent.TButton",
            command=self._start_worker,
        )
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.stop_btn = ttk.Button(
            button_wrap,
            text="■ Stop Scanner",
            style="Warn.TButton",
            command=self._stop_worker,
        )
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self.clear_btn = ttk.Button(
            button_wrap,
            text="Clear Feed",
            style="Subtle.TButton",
            command=lambda: self.log_text.delete("1.0", "end"),
        )
        if self.clear_icon_image is not None:
            self.clear_btn.configure(image=self.clear_icon_image, compound="left")
        self.clear_btn.grid(row=0, column=2, sticky="ew")

        webhook_row = tk.Frame(controls, bg=self.PANEL)
        webhook_row.grid(row=2, column=0, sticky="ew", pady=(8, 2))
        webhook_row.grid_columnconfigure(0, weight=0)
        webhook_row.grid_columnconfigure(1, weight=1)
        webhook_row.grid_columnconfigure(2, weight=0)
        webhook_row.grid_columnconfigure(3, weight=0)

        tk.Label(
            webhook_row,
            text="🔗 Webhook",
            fg=self.MUTED,
            bg=self.PANEL,
            font=self.font_panel_button,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.webhook_entry = ttk.Entry(webhook_row, textvariable=self.webhook_var, font=self.font_mono_small)
        self.webhook_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8), ipady=3)

        self.webhook_save_btn = ttk.Button(
            webhook_row,
            text="Save",
            style="Accent.TButton",
            command=self._save_webhook,
        )
        self.webhook_save_btn.grid(row=0, column=2, padx=(0, 6), sticky="ew")

        self.webhook_test_btn = ttk.Button(
            webhook_row,
            text="Test",
            style="Subtle.TButton",
            command=self._test_webhook,
        )
        self.webhook_test_btn.grid(row=0, column=3, sticky="ew")

        log_frame = tk.Frame(right_panel, bg=self.PANEL_ALT, highlightthickness=2, highlightbackground="#2E5B84")
        log_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            bg="#061126",
            fg=self.TEXT,
            insertbackground=self.ACCENT,
            relief="flat",
            wrap="word",
            font=self.font_log,
            padx=10,
            pady=10,
            state="normal",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.configure(selectbackground="#2C4B72", selectforeground="#F2F8FF")

        self.log_text.tag_configure("info", foreground="#DDE9FA")
        self.log_text.tag_configure("system", foreground="#8EDCFF")
        self.log_text.tag_configure("success", foreground="#87E6A2")
        self.log_text.tag_configure("warn", foreground="#FFD48A")
        self.log_text.tag_configure("danger", foreground="#FF9D9D")
        self.log_text.tag_configure("world", foreground="#9ED6FF")

        footer = tk.Frame(self.root, bg="#09152A", height=34)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        footer.grid_propagate(False)

        discord_wrap = tk.Frame(footer, bg="#09152A")
        discord_wrap.grid(row=0, column=0, sticky="e", padx=14, pady=4)

        self.discord_icon_canvas = tk.Canvas(
            discord_wrap,
            width=28,
            height=28,
            bg="#09152A",
            highlightthickness=0,
            cursor="hand2" if self.discord_handle else "arrow",
        )
        self.discord_icon_canvas.pack(side="left", padx=(0, 8))
        self._draw_discord_badge_icon()

        self.discord_label = tk.Label(
            discord_wrap,
            text=f"Discord: {self.discord_handle}" if self.discord_handle else "Discord: not set",
            fg="#CFE0FF",
            bg="#09152A",
            font=self.font_panel_button,
            cursor="hand2" if self.discord_handle else "arrow",
        )
        self.discord_label.pack(side="left")

        if self.discord_handle:
            self.discord_icon_canvas.bind("<Button-1>", self._copy_discord_handle)
            self.discord_label.bind("<Button-1>", self._copy_discord_handle)

        self._attach_hover_glow(self.start_btn, "Accent.TButton", "AccentGlow.TButton")
        self._attach_hover_glow(self.stop_btn, "Warn.TButton", "WarnGlow.TButton")
        self._attach_hover_glow(self.clear_btn, "Subtle.TButton", "SubtleGlow.TButton")
        self._attach_hover_glow(self.webhook_save_btn, "Accent.TButton", "AccentGlow.TButton")
        self._attach_hover_glow(self.webhook_test_btn, "Subtle.TButton", "SubtleGlow.TButton")
        self._attach_hover_glow(self.copy_player_btn, "Subtle.TButton", "SubtleGlow.TButton")

    def _create_stability_gif_card(self, parent, column: int, title: str, value, subvalue):
        frame = tk.Frame(
            parent,
            bg=self.PANEL,
            highlightthickness=1,
            highlightbackground="#26436C",
            padx=12,
            pady=8,
        )
        frame.grid(row=0, column=column, sticky="nsew", padx=4)

        tk.Label(
            frame,
            text=title,
            fg=self.MUTED,
            bg=self.PANEL,
            font=self.font_panel,
            anchor="w",
        ).pack(anchor="w")

        visual_wrap = tk.Frame(
            frame,
            bg="#0B162B",
            highlightthickness=1,
            highlightbackground="#1F3C60",
            padx=4,
            pady=3,
        )
        visual_wrap.pack(fill="x", pady=(4, 5))

        self.stability_gif_label = tk.Label(
            visual_wrap,
            bg="#0B162B",
            fg="#89A6CC",
            text="GIF",
            font=self.font_mono_small,
            anchor="center",
            justify="center",
        )
        self.stability_gif_label.pack(fill="x")

        value_var = value if isinstance(value, tk.StringVar) else tk.StringVar(value=str(value))
        value_label = tk.Label(
            frame,
            textvariable=value_var,
            fg=self.TEXT,
            bg=self.PANEL,
            font=self.font_panel_value,
            anchor="w",
            justify="left",
        )
        value_label.pack(anchor="w", pady=(1, 2))

        sub_var = subvalue if isinstance(subvalue, tk.StringVar) else tk.StringVar(value=str(subvalue))
        sub_label = tk.Label(
            frame,
            textvariable=sub_var,
            fg=self.MUTED,
            bg=self.PANEL,
            font=self.font_mono_small,
            anchor="w",
            justify="left",
            wraplength=240,
        )
        sub_label.pack(anchor="w")

        self._show_current_stability_gif_frame(force=True)
        return frame, value_label, sub_label

    def _create_metric_card(self, parent, column: int, title: str, value, subvalue):
        frame = tk.Frame(
            parent,
            bg=self.PANEL,
            highlightthickness=1,
            highlightbackground="#26436C",
            padx=12,
            pady=10,
        )
        frame.grid(row=0, column=column, sticky="nsew", padx=4)
        tk.Label(
            frame,
            text=title,
            fg=self.MUTED,
            bg=self.PANEL,
            font=self.font_panel,
            anchor="w",
        ).pack(anchor="w")

        value_var = value if isinstance(value, tk.StringVar) else tk.StringVar(value=str(value))
        value_label = tk.Label(
            frame,
            textvariable=value_var,
            fg=self.TEXT,
            bg=self.PANEL,
            font=self.font_panel_value,
            anchor="w",
            justify="left",
        )
        value_label.pack(anchor="w", pady=(3, 2))

        sub_var = subvalue if isinstance(subvalue, tk.StringVar) else tk.StringVar(value=str(subvalue))
        sub_label = tk.Label(
            frame,
            textvariable=sub_var,
            fg=self.MUTED,
            bg=self.PANEL,
            font=self.font_mono_small,
            anchor="w",
            justify="left",
            wraplength=260,
        )
        sub_label.pack(anchor="w")
        return frame, value_label, sub_label

    def _draw_header(self, _event=None):
        canvas = self.header_canvas
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        canvas.delete("all")

        for y in range(height):
            blend = y / max(1, height - 1)
            color = self._mix_hex("#1E3A66", "#060B18", blend)
            canvas.create_line(0, y, width, y, fill=color)

        canvas.create_rectangle(0, height - 34, width, height, fill="#09152A", outline="")
        title_x = 24
        if self.brand_logo_image is not None:
            badge_size = 94
            badge_x = 14
            badge_y = 10
            badge_cx = badge_x + (badge_size // 2)
            badge_cy = badge_y + (badge_size // 2)
            canvas.create_oval(
                badge_x,
                badge_y,
                badge_x + badge_size,
                badge_y + badge_size,
                fill="#0D1F3D",
                outline="#2A4F7B",
                width=1,
            )
            canvas.create_image(badge_cx, badge_cy, image=self.brand_logo_image, anchor="center")
            if self.brand_wordmark_image is not None:
                name_y = badge_y + badge_size + 30
                canvas.create_image(badge_cx, name_y, image=self.brand_wordmark_image, anchor="n")
            title_x = badge_x + badge_size + 16

        canvas.create_text(
            title_x,
            34,
            anchor="w",
            text="DRAGO'S MODERATION TOOL",
            fill=self.TEXT,
            font=self.font_title,
        )
        canvas.create_text(
            title_x + 2,
            72,
            anchor="w",
            text="World tracking, avatar changes, and stability watch",
            fill=self.MUTED,
            font=self.font_mono_tiny,
        )

        dot_left = width - 150
        dot_top = 28
        dot_size = 30
        halo_pad = 6
        self.status_halo_id = canvas.create_oval(
            dot_left - halo_pad,
            dot_top - halo_pad,
            dot_left + dot_size + halo_pad,
            dot_top + dot_size + halo_pad,
            outline=self._mix_hex("#0B182E", self.stability_color, 0.45),
            width=2,
        )
        self.status_dot_id = canvas.create_oval(
            dot_left,
            dot_top,
            dot_left + dot_size,
            dot_top + dot_size,
            fill=self.stability_color,
            outline="",
        )
        status_text = "● SCANNER ONLINE" if self.status_running else "○ SCANNER OFFLINE"
        self.status_text_id = canvas.create_text(
            width - 26,
            72,
            anchor="e",
            text=status_text,
            fill=self.MUTED,
            font=self.font_panel_title,
        )

    @staticmethod
    def _mix_hex(color_a: str, color_b: str, t: float) -> str:
        t = max(0.0, min(1.0, float(t)))
        a = tuple(int(color_a[i : i + 2], 16) for i in (1, 3, 5))
        b = tuple(int(color_b[i : i + 2], 16) for i in (1, 3, 5))
        out = (
            int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t),
        )
        return f"#{out[0]:02X}{out[1]:02X}{out[2]:02X}"

    def _queue_put(self, event_type: str, payload: object):
        item = (event_type, payload)
        try:
            self.ui_queue.put_nowait(item)
            return
        except Full:
            pass

        try:
            self.ui_queue.get_nowait()
        except Empty:
            pass

        try:
            self.ui_queue.put_nowait(item)
        except Full:
            pass

    def _queue_log_message(self, message: str):
        self._queue_put("log", message)

    def _start_worker(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        self.worker_stop.clear()
        self.logger = self.logger_factory()
        self.worker_thread = threading.Thread(target=self._worker_loop, name="moderation-scanner", daemon=True)
        self.worker_thread.start()

        self.status_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._draw_header()

    def _stop_worker(self):
        self.worker_stop.set()
        self.status_running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self._draw_header()

    def _worker_loop(self):
        if not self.logger:
            return

        logger = self.logger
        safe_print("=" * 72)
        safe_print("Drago's Moderation Tool - GUI")
        safe_print("=" * 72)
        safe_print("Tracking: world, players, avatar changes (VRChat + VRCX)")
        safe_print("Tracking: crash probability, avatar risk, user risk, stability levels")
        safe_print("No IP addresses or network connection scraping")
        safe_print(f"VRChat logs: {Config.VRCHAT_LOG_DIR}")
        safe_print(f"VRCX DB: {Config.VRCX_DB_FILE}")
        safe_print()
        logger.send_startup_webhook()

        last_status = 0.0
        while not self.worker_stop.is_set():
            try:
                logger.scan()
            except Exception as exc:
                safe_print(f"[!] Error in scan: {exc}")

            now = time.time()
            if now - last_status >= Config.STATUS_INTERVAL:
                logger.print_status()
                last_status = now

            self._queue_put("snapshot", logger.get_status_snapshot())

            if self.worker_stop.wait(Config.SCAN_INTERVAL):
                break

        self._queue_put("snapshot", logger.get_status_snapshot())
        safe_print("[!] Scanner loop stopped")

    def _process_queue(self):
        try:
            for _ in range(300):
                event_type, payload = self.ui_queue.get_nowait()
                if event_type == "log":
                    self._append_log(str(payload))
                elif event_type == "snapshot" and isinstance(payload, dict):
                    self._apply_snapshot(payload)
        except Empty:
            pass
        finally:
            if self.root.winfo_exists():
                self.root.after(80, self._process_queue)

    def _apply_snapshot(self, snapshot: Dict[str, object]):
        self.status_running = bool(snapshot.get("running", False))
        self.running_var.set("ONLINE" if self.status_running else "OFFLINE")
        self.world_var.set(str(snapshot.get("world", "(none)")))
        self.players_var.set(str(snapshot.get("players_tracked", 0)))
        self.avatar_var.set(f"{snapshot.get('avatars_known', 0)} avatars known")
        self.stability_var.set(str(snapshot.get("stability_level", "GREEN")))
        self.reason_var.set(str(snapshot.get("stability_reason", "stable")))
        self.rapid_var.set(str(snapshot.get("rapid_switch_count", 0)))
        self.mass_leave_var.set(str(snapshot.get("mass_leave_incidents", 0)))

        self._refresh_player_table(snapshot.get("players", []))
        top_users = snapshot.get("top_users", [])
        self._refresh_user_risk_table(top_users)
        self.risk_level = self._compute_risk_level(top_users)
        self._update_risk_visual()
        self._set_stability_visual(str(snapshot.get("stability_level", "GREEN")))

        if self.status_text_id:
            text = "● SCANNER ONLINE" if self.status_running else "○ SCANNER OFFLINE"
            self.header_canvas.itemconfig(self.status_text_id, text=text)

    @staticmethod
    def _level_max(level_a: str, level_b: str) -> str:
        order = {"GREEN": 0, "YELLOW": 1, "RED": 2}
        a = (level_a or "GREEN").upper()
        b = (level_b or "GREEN").upper()
        return a if order.get(a, 0) >= order.get(b, 0) else b

    @staticmethod
    def _compute_risk_level(users: object) -> str:
        if not isinstance(users, list) or not users:
            return "GREEN"

        max_crash = 0
        max_switches = 0
        max_joins = 0
        for row in users:
            if not isinstance(row, dict):
                continue
            try:
                max_crash = max(max_crash, int(row.get("crash_correlation", 0) or 0))
                max_switches = max(max_switches, int(row.get("switches_recent", 0) or 0))
                max_joins = max(max_joins, int(row.get("joins_recent", 0) or 0))
            except (TypeError, ValueError):
                continue

        # Risk watch is a hint, not a verdict.
        if max_crash >= 4 or max_switches >= 18:
            return "RED"
        if max_crash >= 1 or max_switches >= 10 or max_joins >= 7:
            return "YELLOW"
        return "GREEN"

    def _update_risk_visual(self):
        if not self.risk_header_label:
            return
        level = (self.risk_level or "GREEN").upper()
        if level == "RED":
            color = self.RED
        elif level == "YELLOW":
            color = self.YELLOW
        else:
            color = self.GREEN
        self.risk_header_label.configure(fg=color)

    def _refresh_player_table(self, players: object):
        for row_id in self.players_tree.get_children():
            self.players_tree.delete(row_id)

        if not isinstance(players, list):
            return

        for row in players:
            if not isinstance(row, dict):
                continue
            self.players_tree.insert(
                "",
                "end",
                values=(
                    row.get("username", ""),
                    row.get("user_id", "") or "-",
                    row.get("platform", "Unknown") or "Unknown",
                    row.get("avatar", "(unknown yet)"),
                    row.get("avatar_id", "") or "-",
                    row.get("creator_id", "") or "-",
                ),
            )

    def _copy_selected_player(self):
        selected = self.players_tree.selection()
        if not selected:
            safe_print("[!] Select a player row first.")
            return

        row = self.players_tree.item(selected[0])
        values = list(row.get("values", []))
        while len(values) < 6:
            values.append("")

        username = str(values[0] or "")
        user_id = str(values[1] or "-")
        platform = str(values[2] or "Unknown")
        avatar = str(values[3] or "(unknown yet)")
        avatar_id = str(values[4] or "-")

        text = "\n".join(
            [
                f"Username: {username}",
                f"User ID: {user_id}",
                f"Platform: {platform}",
                f"Avatar: {avatar}",
                f"Avatar ID: {avatar_id}",
            ]
        )
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
            safe_print(f"[+] Copied player details for {username}.")
        except tk.TclError as exc:
            safe_print(f"[!] Clipboard copy failed: {exc}")

    def _save_webhook(self):
        webhook_url = (self.webhook_var.get() or "").strip()
        if webhook_url and "discord.com/api/webhooks/" not in webhook_url:
            safe_print("[!] Warning: webhook URL format does not look like a Discord webhook.")

        if not Config.set_discord_webhook(webhook_url):
            return

        if self.logger is not None:
            self.logger.discord = Discord(Config.DISCORD_WEBHOOK, printer=safe_print)

        if webhook_url:
            safe_print("[+] Discord webhook updated and saved.")
        else:
            safe_print("[!] Discord webhook cleared. Notifications are now disabled.")

    def _test_webhook(self):
        webhook_url = (self.webhook_var.get() or "").strip()
        discord = Discord(webhook_url, printer=safe_print)
        if not discord.enabled:
            safe_print("[!] Webhook is empty. Paste a webhook URL, then click Save.")
            return

        fields = [
            {"name": "VRChat Logs", "value": f"`{Config.VRCHAT_LOG_DIR}`", "inline": False},
            {"name": "VRCX DB", "value": f"`{Config.VRCX_DB_FILE}`", "inline": False},
        ]
        ok = discord.send_embed(
            "Webhook Test",
            "Manual webhook test from Drago's Moderation Tool GUI.",
            0x35D2FF,
            fields,
        )
        if ok:
            safe_print("[+] Webhook test sent successfully.")
        else:
            safe_print("[!] Webhook test failed.")

    def _refresh_user_risk_table(self, users: object):
        for row_id in self.risk_tree.get_children():
            self.risk_tree.delete(row_id)

        if not isinstance(users, list):
            return

        for row in users:
            if not isinstance(row, dict):
                continue
            self.risk_tree.insert(
                "",
                "end",
                values=(
                    row.get("username", ""),
                    row.get("joins_recent", 0),
                    row.get("switches_recent", 0),
                    row.get("crash_correlation", 0),
                ),
            )

    def _append_log(self, message: str, forced_tag: str = ""):
        if not message:
            return

        for line in message.splitlines(keepends=True):
            tag = forced_tag or self._pick_log_tag(line)
            self.log_text.insert("end", line, tag)
        self.log_text.see("end")

    @staticmethod
    def _pick_log_tag(line: str) -> str:
        line_cf = line.casefold()
        if "stability: red" in line_cf or "mass leave" in line_cf:
            return "danger"
        if "stability: yellow" in line_cf or "[!]" in line_cf or "error in scan" in line_cf:
            return "warn"
        if "] +" in line:
            return "success"
        if "] -" in line:
            return "danger"
        if "[world]" in line_cf:
            return "world"
        if "started" in line_cf or "tracking:" in line_cf:
            return "system"
        return "info"

    def _set_stability_visual(self, level: str):
        level_up = (level or "").upper()
        if level_up == "RED":
            self.stability_color = self.RED
        elif level_up == "YELLOW":
            self.stability_color = self.YELLOW
        else:
            self.stability_color = self.GREEN

        glow_mix = 0.42 if level_up == "GREEN" else 0.34
        glow_color = self._mix_hex("#122642", self.stability_color, glow_mix)
        glow_thickness = 2 if level_up == "GREEN" else 1
        self.stability_value_label.configure(fg=self.stability_color)
        self.stability_card.configure(
            highlightthickness=glow_thickness,
            highlightbackground=glow_color,
            highlightcolor=glow_color,
        )
        if self.status_dot_id:
            self.header_canvas.itemconfig(self.status_dot_id, fill=self.stability_color)
        if self.status_halo_id:
            halo_color = self._mix_hex("#0B172D", self.stability_color, 0.55)
            self.header_canvas.itemconfig(self.status_halo_id, outline=halo_color)
        # GIF follows stability; the risk header colors itself.
        self._show_current_stability_gif_frame(level_up, force=(level_up != self.stability_gif_level))

    def _show_current_stability_gif_frame(self, level: str = "", force: bool = False):
        if self.stability_gif_label is None:
            return

        level_up = (level or self.stability_gif_level or "GREEN").upper()
        if level_up not in ("GREEN", "YELLOW", "RED"):
            level_up = "GREEN"

        if force or level_up != self.stability_gif_level:
            self.stability_gif_level = level_up
            self.stability_gif_frame_index = 0

        frames = self.stability_gif_frames.get(self.stability_gif_level, [])
        if not frames and self.stability_gif_frames:
            self.stability_gif_level = "GREEN" if "GREEN" in self.stability_gif_frames else next(iter(self.stability_gif_frames))
            frames = self.stability_gif_frames.get(self.stability_gif_level, [])
            self.stability_gif_frame_index = 0

        if not frames:
            self.stability_gif_label.configure(image="", text="GIF MISSING")
            self.stability_gif_label.image = None
            return

        self.stability_gif_frame_index %= len(frames)
        frame = frames[self.stability_gif_frame_index]
        self.stability_gif_label.configure(image=frame, text="")
        self.stability_gif_label.image = frame

    def _animate_stability_gif(self):
        if not self.root.winfo_exists():
            return

        frames = self.stability_gif_frames.get(self.stability_gif_level, [])
        delays = self.stability_gif_delays.get(self.stability_gif_level, [])

        if not frames:
            self._show_current_stability_gif_frame(force=False)
            self.stability_gif_after_id = self.root.after(180, self._animate_stability_gif)
            return

        self.stability_gif_frame_index = (self.stability_gif_frame_index + 1) % len(frames)
        self._show_current_stability_gif_frame(force=False)

        if delays and self.stability_gif_frame_index < len(delays):
            delay_ms = delays[self.stability_gif_frame_index]
        else:
            delay_ms = 70
        delay_ms = max(35, min(220, int(delay_ms)))
        self.stability_gif_after_id = self.root.after(delay_ms, self._animate_stability_gif)

    def _animate_status_dot(self):
        self._pulse_phase += 0.22

        if self.status_dot_id:
            base_color = self.stability_color if self.status_running else "#425673"
            pulse = 0.55 + 0.45 * ((math.sin(self._pulse_phase) + 1.0) / 2.0)
            if not self.status_running:
                pulse = 0.25 + (pulse * 0.25)
            color = self._mix_hex("#0A1122", base_color, pulse)
            self.header_canvas.itemconfig(self.status_dot_id, fill=color)
            if self.status_halo_id:
                halo_strength = 0.30 + 0.70 * ((math.sin(self._pulse_phase) + 1.0) / 2.0)
                if not self.status_running:
                    halo_strength *= 0.45
                halo_color = self._mix_hex("#0A1224", base_color, halo_strength)
                halo_width = 1 + int(halo_strength * 2.0)
                self.header_canvas.itemconfig(self.status_halo_id, outline=halo_color, width=halo_width)

        if self.root.winfo_exists():
            self.root.after(120, self._animate_status_dot)

    def on_close(self):
        self.worker_stop.set()
        if self.stability_gif_after_id:
            try:
                self.root.after_cancel(self.stability_gif_after_id)
            except tk.TclError:
                pass
            self.stability_gif_after_id = None
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2.0)
        remove_print_listener(self._queue_log_message)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


