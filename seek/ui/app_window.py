"""Tkinter user interface for saving YouTube audio and metadata."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from seek.core.engine import (
    DownloadEvent,
    DownloadFailedError,
    MissingDependencyError,
    UserCancelledError,
    download_urls,
)
from seek.models.links import (
    is_youtube_url,
    normalize_youtube_url,
    parse_url_entries,
)
import seek.core.spotify as spotify


APP_TITLE = "SEEK"
WINDOW_TITLE = "SEEK — YouTube & Spotify Audio Downloader"
APP_VERSION = "1.0"

COLORS = {
    "app_bg": "#F3F6FB",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFD",
    "nav": "#11172A",
    "nav_soft": "#1A2238",
    "hero": "#19162F",
    "ink": "#172033",
    "muted": "#667085",
    "muted_light": "#AAB5CC",
    "border": "#DCE3EF",
    "primary": "#6D4AFF",
    "primary_hover": "#5B38EA",
    "cyan": "#13B8D4",
    "success": "#16A36A",
    "warning": "#D97706",
    "danger": "#D9475A",
    "console": "#0D1325",
    "console_text": "#DDE7FF",
}


class YouTubeAudioApp:
    """Small, responsive desktop UI around the downloader service."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(WINDOW_TITLE)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        tk_scaling = float(self.root.tk.call("tk", "scaling"))
        display_scale = max(1.0, min(2.0, tk_scaling / (96 / 72)))
        logical_screen_width = screen_width / display_scale
        logical_screen_height = screen_height / display_scale
        logical_width = min(
            1040,
            max(780, logical_screen_width - 80),
        )
        logical_height = min(
            920,
            max(650, logical_screen_height - 100),
        )
        window_width = round(logical_width * display_scale)
        window_height = round(logical_height * display_scale)
        self.compact_layout = logical_height < 800
        window_x = max(0, (screen_width - window_width) // 2)
        window_y = max(0, (screen_height - window_height) // 2)
        self.root.geometry(
            f"{window_width}x{window_height}+{window_x}+{window_y}"
        )
        self.root.minsize(
            min(round(820 * display_scale), window_width),
            min(round(600 * display_scale), window_height),
        )
        self.root.configure(background=COLORS["app_bg"])

        default_output = Path.home() / "Downloads" / "YouTube Audio"
        self.output_var = tk.StringVar(value=str(default_output))
        self.status_var = tk.StringVar(
            value="Ready to build your audio library"
        )
        self.count_var = tk.StringVar(value="0 videos saved")
        self.link_count_var = tk.StringVar(value="0")
        self.link_badge_var = tk.StringVar(value="0 links")
        self.saved_stat_var = tk.StringVar(value="0")
        self.skipped_stat_var = tk.StringVar(value="0")
        self.visual_state_var = tk.StringVar(value="READY")
        self.activity_visible_var = tk.BooleanVar(
            value=logical_height >= 760
        )

        self.events: queue.Queue[DownloadEvent] = queue.Queue()
        self.config = DownloadConfig()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        self.close_requested = False
        self.last_saved_path: Path | None = None
        self.saved_count = 0
        self.skipped_count = 0
        self.progress_is_indeterminate = False
        self.visual_state = "ready"
        self.animation_job: str | None = None
        self.animation_step = 0

        self._configure_styles()
        self._build_menu()
        self._create_app_icon()
        self._build_ui()
        self._build_url_context_menu()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_events)
        self.url_input.focus_set()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(
            "Primary.TButton",
            background=COLORS["primary"],
            foreground="#FFFFFF",
            borderwidth=0,
            padding=(18, 10),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[
                ("pressed", COLORS["primary_hover"]),
                ("active", COLORS["primary_hover"]),
                ("disabled", "#B9AFE9"),
            ],
            foreground=[("disabled", "#F4F1FF")],
        )
        style.configure(
            "Secondary.TButton",
            background=COLORS["surface_alt"],
            foreground=COLORS["ink"],
            bordercolor=COLORS["border"],
            borderwidth=1,
            padding=(13, 8),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Secondary.TButton",
            background=[
                ("active", "#EDF1F8"),
                ("pressed", "#E5EAF4"),
                ("disabled", "#F5F6F9"),
            ],
            foreground=[("disabled", "#A1A9B7")],
        )
        style.configure(
            "Compact.TButton",
            background=COLORS["surface_alt"],
            foreground=COLORS["muted"],
            borderwidth=0,
            padding=(10, 5),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Compact.TButton",
            background=[("active", "#EAEFFA")],
            foreground=[("active", COLORS["primary"])],
        )
        style.configure(
            "Danger.TButton",
            background="#FFF1F3",
            foreground=COLORS["danger"],
            bordercolor="#FFD3DA",
            borderwidth=1,
            padding=(15, 9),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Danger.TButton",
            background=[
                ("active", "#FFE4E8"),
                ("pressed", "#FFD7DD"),
                ("disabled", "#F7F3F4"),
            ],
            foreground=[("disabled", "#C7A5AB")],
        )
        style.configure(
            "Sidebar.TButton",
            background=COLORS["nav"],
            foreground=COLORS["muted_light"],
            borderwidth=0,
            anchor="w",
            padding=(16, 10),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Sidebar.TButton",
            background=[("active", COLORS["nav_soft"])],
            foreground=[("active", "#FFFFFF")],
        )
        style.configure(
            "SidebarActive.TButton",
            background=COLORS["nav_soft"],
            foreground="#FFFFFF",
            borderwidth=0,
            anchor="w",
            padding=(16, 10),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "SidebarActive.TButton",
            background=[("active", "#222C46")],
        )
        style.configure(
            "Modern.TEntry",
            fieldbackground=COLORS["surface_alt"],
            foreground=COLORS["ink"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=(10, 8),
        )
        style.map(
            "Modern.TEntry",
            bordercolor=[("focus", COLORS["primary"])],
            lightcolor=[("focus", COLORS["primary"])],
            darkcolor=[("focus", COLORS["primary"])],
        )
        style.configure(
            "Seek.Horizontal.TProgressbar",
            troughcolor="#E7EAF2",
            background=COLORS["primary"],
            lightcolor=COLORS["primary"],
            darkcolor=COLORS["primary"],
            bordercolor="#E7EAF2",
            thickness=8,
        )

    def _build_menu(self) -> None:
        self.menu_bar = tk.Menu(self.root)

        self.file_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.file_menu.add_command(
            label="New session",
            accelerator="Ctrl+N",
            command=self._new_session,
        )
        self.file_menu.add_separator()
        self.file_menu.add_command(
            label="Choose destination…",
            accelerator="Ctrl+O",
            command=self._choose_output,
        )
        self.file_menu.add_command(
            label="Open destination",
            command=self._open_output,
        )
        self.file_menu.add_separator()
        self.file_menu.add_command(
            label="General settings…",
            command=self._open_general_settings,
        )
        self.file_menu.add_command(
            label="Spotify settings…",
            command=self._open_spotify_settings,
        )
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit SEEK", command=self._on_close)
        self.menu_bar.add_cascade(label="File", menu=self.file_menu)

        self.edit_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.edit_menu.add_command(
            label="Paste links",
            accelerator="Ctrl+V",
            command=self._paste_links,
        )
        self.edit_menu.add_command(
            label="Select all links",
            accelerator="Ctrl+A",
            command=self._select_all_links,
        )
        self.edit_menu.add_command(
            label="Clear links",
            command=self._clear_links,
        )
        self.menu_bar.add_cascade(label="Edit", menu=self.edit_menu)

        self.download_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.download_menu.add_command(
            label="Start download",
            accelerator="Ctrl+Enter",
            command=self._start_download,
        )
        self.download_menu.add_command(
            label="Cancel",
            accelerator="Esc",
            command=self._cancel_download,
            state="disabled",
        )
        self.menu_bar.add_cascade(
            label="Download",
            menu=self.download_menu,
        )

        self.view_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.view_menu.add_checkbutton(
            label="Show activity",
            variable=self.activity_visible_var,
            command=self._apply_activity_visibility,
        )
        self.menu_bar.add_cascade(label="View", menu=self.view_menu)

        self.help_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.help_menu.add_command(
            label="Keyboard shortcuts",
            command=self._show_shortcuts,
        )
        self.help_menu.add_command(
            label="About SEEK",
            command=self._show_about,
        )
        self.menu_bar.add_cascade(label="Help", menu=self.help_menu)
        self.root.configure(menu=self.menu_bar)

    def _create_app_icon(self) -> None:
        icon = tk.PhotoImage(width=32, height=32)
        icon.put(COLORS["primary"], to=(0, 0, 32, 32))
        icon.put(COLORS["cyan"], to=(0, 0, 5, 32))
        for rectangle in (
            (9, 7, 24, 10),
            (7, 8, 11, 17),
            (9, 14, 24, 18),
            (21, 16, 25, 25),
            (8, 23, 23, 27),
        ):
            icon.put("#FFFFFF", to=rectangle)
        self.app_icon = icon
        try:
            self.root.iconphoto(True, icon)
        except tk.TclError:
            pass

    def _build_ui(self) -> None:
        shell = tk.Frame(self.root, background=COLORS["app_bg"])
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        sidebar_width = 194 if self.compact_layout else 206
        sidebar = tk.Frame(
            shell,
            background=COLORS["nav"],
            width=sidebar_width,
        )
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(8, weight=1)
        self.sidebar = sidebar

        brand = tk.Frame(sidebar, background=COLORS["nav"])
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 24))
        tk.Label(
            brand,
            image=self.app_icon,
            background=COLORS["nav"],
        ).grid(row=0, column=0, rowspan=2, padx=(0, 10))
        tk.Label(
            brand,
            text="SEEK",
            background=COLORS["nav"],
            foreground="#FFFFFF",
            font=("Segoe UI", 18, "bold"),
        ).grid(row=0, column=1, sticky="sw")
        tk.Label(
            brand,
            text="AUDIO TOOLKIT",
            background=COLORS["nav"],
            foreground=COLORS["muted_light"],
            font=("Segoe UI", 7, "bold"),
        ).grid(row=1, column=1, sticky="nw")

        tk.Label(
            sidebar,
            text="WORKSPACE",
            background=COLORS["nav"],
            foreground="#75819B",
            font=("Segoe UI", 7, "bold"),
        ).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 6))

        ttk.Button(
            sidebar,
            text="  DOWNLOADS",
            style="SidebarActive.TButton",
            command=self._focus_download,
            cursor="hand2",
        ).grid(row=2, column=0, sticky="ew", padx=10)
        ttk.Button(
            sidebar,
            text="  ACTIVITY",
            style="Sidebar.TButton",
            command=self._toggle_activity,
            cursor="hand2",
        ).grid(row=3, column=0, sticky="ew", padx=10, pady=(2, 0))
        ttk.Button(
            sidebar,
            text="  OPEN LIBRARY",
            style="Sidebar.TButton",
            command=self._open_output,
            cursor="hand2",
        ).grid(row=4, column=0, sticky="ew", padx=10, pady=(2, 0))
        ttk.Button(
            sidebar,
            text="  ABOUT SEEK",
            style="Sidebar.TButton",
            command=self._show_about,
            cursor="hand2",
        ).grid(row=5, column=0, sticky="ew", padx=10, pady=(2, 0))

        tip = tk.Frame(
            sidebar,
            background=COLORS["nav_soft"],
            padx=13,
            pady=12,
        )
        tip.grid(row=9, column=0, sticky="sew", padx=14, pady=(12, 12))
        tk.Label(
            tip,
            text="QUICK TIP",
            background=COLORS["nav_soft"],
            foreground=COLORS["cyan"],
            font=("Segoe UI", 7, "bold"),
        ).pack(anchor="w")
        tk.Label(
            tip,
            text=(
                "Paste YouTube channels/playlists or Spotify tracks/"
                "playlists. SEEK remembers completed work."
            ),
            background=COLORS["nav_soft"],
            foreground="#CAD3E7",
            justify="left",
            wraplength=sidebar_width - 54,
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(5, 0))
        tk.Label(
            sidebar,
            text=f"SEEK {APP_VERSION}",
            background=COLORS["nav"],
            foreground="#68758F",
            font=("Segoe UI", 7),
        ).grid(row=10, column=0, sticky="w", padx=22, pady=(0, 16))

        main_padding = 14 if self.compact_layout else 20
        main = tk.Frame(
            shell,
            background=COLORS["app_bg"],
            padx=main_padding,
            pady=main_padding,
        )
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(
            5,
            weight=1,
            minsize=70 if self.activity_visible_var.get() else 0,
        )
        main.bind("<Configure>", self._on_main_resize)
        self.main = main

        topbar = tk.Frame(main, background=COLORS["app_bg"])
        topbar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        topbar.columnconfigure(0, weight=1)
        tk.Label(
            topbar,
            text="Download studio",
            background=COLORS["app_bg"],
            foreground=COLORS["ink"],
            font=("Segoe UI", 19, "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            topbar,
            text="Turn YouTube and Spotify links into a tidy, offline audio library.",
            background=COLORS["app_bg"],
            foreground=COLORS["muted"],
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, sticky="w", pady=(1, 0))

        self.state_pill = tk.Frame(
            topbar,
            background="#EEF1F6",
            padx=10,
            pady=5,
        )
        self.state_pill.grid(row=0, column=1, rowspan=2, sticky="e")
        self.state_dot = tk.Label(
            self.state_pill,
            text="●",
            background="#EEF1F6",
            foreground=COLORS["muted"],
            font=("Segoe UI", 8),
        )
        self.state_dot.pack(side="left")
        self.state_text = tk.Label(
            self.state_pill,
            textvariable=self.visual_state_var,
            background="#EEF1F6",
            foreground=COLORS["muted"],
            font=("Segoe UI", 8, "bold"),
        )
        self.state_text.pack(side="left", padx=(5, 0))

        hero = tk.Frame(
            main,
            background=COLORS["hero"],
            padx=18,
            pady=12 if self.compact_layout else 15,
        )
        hero.grid(row=1, column=0, sticky="ew", pady=(0, 11))
        hero.columnconfigure(0, weight=1)
        hero_copy = tk.Frame(hero, background=COLORS["hero"])
        hero_copy.grid(row=0, column=0, sticky="w")
        tk.Label(
            hero_copy,
            text="YOUTUBE + SPOTIFY  →  MP3",
            background=COLORS["primary"],
            foreground="#FFFFFF",
            padx=8,
            pady=2,
            font=("Segoe UI", 7, "bold"),
        ).pack(anchor="w")
        tk.Label(
            hero_copy,
            text="Find it. Save it. Keep listening.",
            background=COLORS["hero"],
            foreground="#FFFFFF",
            font=("Segoe UI", 15 if self.compact_layout else 17, "bold"),
        ).pack(anchor="w", pady=(7, 1))
        tk.Label(
            hero_copy,
            text="Resumable downloads • clean folders • zero repeat work",
            background=COLORS["hero"],
            foreground="#B9C3DA",
            font=("Segoe UI", 8),
        ).pack(anchor="w")
        self.visualizer = tk.Canvas(
            hero,
            width=150,
            height=60,
            background=COLORS["hero"],
            highlightthickness=0,
        )
        self.visualizer.grid(row=0, column=1, sticky="e", padx=(14, 2))

        stats = tk.Frame(main, background=COLORS["app_bg"])
        stats.grid(row=2, column=0, sticky="ew", pady=(0, 11))
        for column in range(3):
            stats.columnconfigure(column, weight=1, uniform="stats")
        self._make_stat_card(
            stats,
            0,
            "LINKS IN QUEUE",
            self.link_count_var,
            COLORS["primary"],
        )
        self._make_stat_card(
            stats,
            1,
            "SAVED THIS RUN",
            self.saved_stat_var,
            COLORS["success"],
        )
        self._make_stat_card(
            stats,
            2,
            "ALREADY COMPLETE",
            self.skipped_stat_var,
            COLORS["cyan"],
        )

        form_padding = 11 if self.compact_layout else 14
        form = tk.Frame(
            main,
            background=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=form_padding,
            pady=form_padding,
        )
        form.grid(row=3, column=0, sticky="ew", pady=(0, 11))
        form.columnconfigure(0, weight=1)
        self.form_card = form

        form_header = tk.Frame(form, background=COLORS["surface"])
        form_header.grid(row=0, column=0, sticky="ew")
        form_header.columnconfigure(0, weight=1)
        heading = tk.Frame(form_header, background=COLORS["surface"])
        heading.grid(row=0, column=0, sticky="w")
        tk.Label(
            heading,
            text="Add YouTube or Spotify links",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")
        tk.Label(
            heading,
            textvariable=self.link_badge_var,
            background="#EEEAFE",
            foreground=COLORS["primary"],
            padx=7,
            pady=2,
            font=("Segoe UI", 7, "bold"),
        ).pack(side="left", padx=(8, 0))

        header_actions = tk.Frame(
            form_header,
            background=COLORS["surface"],
        )
        header_actions.grid(row=0, column=1, sticky="e")
        self.paste_button = ttk.Button(
            header_actions,
            text="Paste",
            style="Compact.TButton",
            command=self._paste_links,
            cursor="hand2",
        )
        self.paste_button.pack(side="left")
        self.clear_button = ttk.Button(
            header_actions,
            text="Clear",
            style="Compact.TButton",
            command=self._clear_links,
            cursor="hand2",
        )
        self.clear_button.pack(side="left", padx=(4, 0))

        self.url_border = tk.Frame(
            form,
            background=COLORS["border"],
            padx=1,
            pady=1,
        )
        self.url_border.grid(row=1, column=0, sticky="ew", pady=(8, 10))
        self.url_border.columnconfigure(0, weight=1)
        self.url_input = scrolledtext.ScrolledText(
            self.url_border,
            height=2 if self.compact_layout else 3,
            wrap="word",
            undo=True,
            borderwidth=0,
            relief="flat",
            background=COLORS["surface_alt"],
            foreground=COLORS["ink"],
            insertbackground=COLORS["primary"],
            selectbackground="#DCD4FF",
            selectforeground=COLORS["ink"],
            padx=9,
            pady=7,
            font=("Segoe UI", 9),
        )
        self.url_input.grid(row=0, column=0, sticky="ew")
        self.url_input.bind("<<Modified>>", self._on_links_modified)
        self.url_input.bind(
            "<FocusIn>",
            lambda _event: self._set_url_focus(True),
        )
        self.url_input.bind(
            "<FocusOut>",
            lambda _event: self._set_url_focus(False),
        )
        self.url_input.edit_modified(False)

        tk.Frame(
            form,
            background=COLORS["border"],
            height=1,
        ).grid(row=2, column=0, sticky="ew", pady=(0, 9))

        destination_header = tk.Frame(
            form,
            background=COLORS["surface"],
        )
        destination_header.grid(row=3, column=0, sticky="ew")
        tk.Label(
            destination_header,
            text="Save destination",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        tk.Label(
            destination_header,
            text="Each video gets its own folder",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(8, 0))

        destination = tk.Frame(form, background=COLORS["surface"])
        destination.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        destination.columnconfigure(0, weight=1)
        self.output_entry = ttk.Entry(
            destination,
            textvariable=self.output_var,
            style="Modern.TEntry",
        )
        self.output_entry.grid(row=0, column=0, sticky="ew")
        self.browse_button = ttk.Button(
            destination,
            text="Browse…",
            style="Secondary.TButton",
            command=self._choose_output,
            cursor="hand2",
        )
        self.browse_button.grid(row=0, column=1, padx=(7, 0))
        self.open_button = ttk.Button(
            destination,
            text="Open",
            style="Secondary.TButton",
            command=self._open_output,
            cursor="hand2",
        )
        self.open_button.grid(row=0, column=2, padx=(7, 0))

        action_card = tk.Frame(
            main,
            background=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=13,
            pady=11,
        )
        action_card.grid(row=4, column=0, sticky="ew", pady=(0, 11))
        action_card.columnconfigure(2, weight=1)
        self.download_button = ttk.Button(
            action_card,
            text="Download audio",
            style="Primary.TButton",
            command=self._start_download,
            cursor="hand2",
        )
        self.download_button.grid(row=0, column=0, rowspan=2, sticky="ns")
        self.cancel_button = ttk.Button(
            action_card,
            text="Cancel",
            style="Danger.TButton",
            command=self._cancel_download,
            state="disabled",
            cursor="hand2",
        )
        self.cancel_button.grid(
            row=0,
            column=1,
            rowspan=2,
            sticky="ns",
            padx=(8, 14),
        )
        self.retry_button = ttk.Button(
            action_card,
            text="Retry Failed",
            style="Primary.TButton",
            command=self._retry_failed,
            state="disabled",
            cursor="hand2",
        )
        self.retry_button.grid(
            row=0,
            column=2,
            rowspan=2,
            sticky="ns",
            padx=(0, 14),
        )
        self.retry_button.grid_remove() # Hidden by default
        self.progress = ttk.Progressbar(
            action_card,
            style="Seek.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
            value=0,
        )
        self.progress.grid(row=0, column=3, sticky="ew", pady=(2, 0))
        status_row = tk.Frame(action_card, background=COLORS["surface"])
        status_row.grid(row=1, column=3, sticky="ew", pady=(7, 0))
        status_row.columnconfigure(0, weight=1)
        self.status_label = tk.Label(
            status_row,
            textvariable=self.status_var,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            anchor="w",
            justify="left",
            font=("Segoe UI", 8, "bold"),
        )
        self.status_label.grid(row=0, column=0, sticky="ew")
        tk.Label(
            status_row,
            text="CTRL+ENTER",
            background=COLORS["surface"],
            foreground="#98A2B3",
            font=("Segoe UI", 7, "bold"),
        ).grid(row=0, column=1, sticky="e")

        self.activity_card = tk.Frame(
            main,
            background=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=11,
            pady=9,
        )
        self.activity_card.grid(row=5, column=0, sticky="nsew")
        self.activity_card.columnconfigure(0, weight=1)
        self.activity_card.rowconfigure(1, weight=1)
        activity_header = tk.Frame(
            self.activity_card,
            background=COLORS["surface"],
        )
        activity_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        activity_header.columnconfigure(0, weight=1)
        tk.Label(
            activity_header,
            text="Activity",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            activity_header,
            text="Clear log",
            style="Compact.TButton",
            command=self._clear_log,
            cursor="hand2",
        ).grid(row=0, column=1, sticky="e")
        ttk.Button(
            activity_header,
            text="Hide",
            style="Compact.TButton",
            command=lambda: self._toggle_activity(False),
            cursor="hand2",
        ).grid(row=0, column=2, sticky="e", padx=(4, 0))
        self.log = scrolledtext.ScrolledText(
            self.activity_card,
            height=3 if self.compact_layout else 5,
            wrap="word",
            state="disabled",
            borderwidth=0,
            relief="flat",
            background=COLORS["console"],
            foreground=COLORS["console_text"],
            insertbackground="#FFFFFF",
            selectbackground="#314064",
            padx=10,
            pady=8,
            font=("Consolas", 8),
        )
        self.log.grid(row=1, column=0, sticky="nsew")
        self.log.tag_configure("info", foreground=COLORS["console_text"])
        self.log.tag_configure("warning", foreground="#FBC477")
        self.log.tag_configure("error", foreground="#FF96A5")
        self.log.tag_configure("success", foreground="#72E0AF")
        self.log.tag_configure("skip", foreground="#72D8E8")

        footer = tk.Frame(main, background=COLORS["app_bg"])
        footer.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        tk.Label(
            footer,
            text="Download only content that you have permission to use.",
            background=COLORS["app_bg"],
            foreground=COLORS["muted"],
            font=("Segoe UI", 7),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            footer,
            text="Esc to cancel  •  Progress is saved automatically",
            background=COLORS["app_bg"],
            foreground="#98A2B3",
            font=("Segoe UI", 7),
        ).grid(row=0, column=1, sticky="e")

        self._apply_activity_visibility()
        self._set_visual_state("ready")
        self.root.after_idle(self._draw_visualizer)

    def _make_stat_card(
        self,
        parent: tk.Widget,
        column: int,
        label: str,
        variable: tk.StringVar,
        accent: str,
    ) -> None:
        padx = (0, 5) if column == 0 else (5, 5) if column == 1 else (5, 0)
        card = tk.Frame(
            parent,
            background=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        card.grid(row=0, column=column, sticky="ew", padx=padx)
        tk.Frame(card, background=accent, width=4).pack(
            side="left",
            fill="y",
        )
        copy = tk.Frame(
            card,
            background=COLORS["surface"],
            padx=11,
            pady=7,
        )
        copy.pack(side="left", fill="both", expand=True)
        tk.Label(
            copy,
            text=label,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=("Segoe UI", 7, "bold"),
        ).pack(anchor="w")
        tk.Label(
            copy,
            textvariable=variable,
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w")

    def _build_url_context_menu(self) -> None:
        self.url_context_menu = tk.Menu(self.root, tearoff=False)
        self.url_context_menu.add_command(
            label="Cut",
            command=lambda: self.url_input.event_generate("<<Cut>>"),
        )
        self.url_context_menu.add_command(
            label="Copy",
            command=lambda: self.url_input.event_generate("<<Copy>>"),
        )
        self.url_context_menu.add_command(
            label="Paste",
            command=self._paste_links,
        )
        self.url_context_menu.add_separator()
        self.url_context_menu.add_command(
            label="Select all",
            command=self._select_all_links,
        )
        self.url_input.bind("<Button-3>", self._show_url_context_menu)

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-Return>", self._start_from_shortcut)
        self.root.bind_all("<Escape>", self._cancel_from_shortcut)
        self.root.bind_all("<Control-n>", lambda _event: self._new_session())
        self.root.bind_all("<Control-o>", lambda _event: self._choose_output())
        self.root.bind_all("<Control-l>", lambda _event: self._focus_download())
        self.root.bind_all("<F1>", lambda _event: self._show_shortcuts())

    def _show_url_context_menu(self, event: tk.Event) -> str:
        try:
            self.url_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.url_context_menu.grab_release()
        return "break"

    def _on_main_resize(self, event: tk.Event) -> None:
        if hasattr(self, "status_label"):
            self.status_label.configure(wraplength=max(260, event.width - 380))

    def _draw_visualizer(self) -> None:
        if not hasattr(self, "visualizer"):
            return
        self.visualizer.delete("all")
        width = max(120, self.visualizer.winfo_width())
        height = max(48, self.visualizer.winfo_height())
        base = (18, 34, 24, 45, 29, 39, 20, 31, 16)
        colors = (
            COLORS["primary"],
            "#8A6CFF",
            COLORS["cyan"],
        )
        bar_width = 7
        gap = 6
        total_width = len(base) * bar_width + (len(base) - 1) * gap
        start_x = max(4, (width - total_width) // 2)
        for index, baseline in enumerate(base):
            if self.visual_state in {"working", "cancelling"}:
                offset = (index + self.animation_step) % len(base)
                bar_height = base[offset]
            else:
                bar_height = baseline
            x1 = start_x + index * (bar_width + gap)
            y1 = (height - bar_height) // 2
            self.visualizer.create_rectangle(
                x1,
                y1,
                x1 + bar_width,
                y1 + bar_height,
                fill=colors[index % len(colors)],
                outline="",
            )

    def _animate_visualizer(self) -> None:
        self.animation_job = None
        if self.visual_state not in {"working", "cancelling"}:
            return
        self.animation_step = (self.animation_step + 1) % 9
        pulse_color = (
            COLORS["cyan"]
            if self.animation_step % 2
            else COLORS["primary"]
        )
        if self.visual_state == "cancelling":
            pulse_color = COLORS["warning"]
        try:
            self.state_dot.configure(foreground=pulse_color)
            self._draw_visualizer()
            self.animation_job = self.root.after(
                220,
                self._animate_visualizer,
            )
        except tk.TclError:
            self.animation_job = None

    def _stop_animation(self) -> None:
        if self.animation_job is not None:
            try:
                self.root.after_cancel(self.animation_job)
            except tk.TclError:
                pass
            self.animation_job = None

    def _set_visual_state(self, state: str) -> None:
        states = {
            "ready": ("READY", COLORS["muted"], "#EEF1F6"),
            "working": ("WORKING", COLORS["primary"], "#EEEAFE"),
            "cancelling": ("STOPPING", COLORS["warning"], "#FFF3E5"),
            "complete": ("COMPLETE", COLORS["success"], "#EAF8F1"),
            "warning": ("ATTENTION", COLORS["warning"], "#FFF3E5"),
            "failed": ("FAILED", COLORS["danger"], "#FFF0F2"),
            "cancelled": ("STOPPED", COLORS["muted"], "#EEF1F6"),
        }
        label, color, background = states.get(state, states["ready"])
        self.visual_state = state
        self.visual_state_var.set(label)
        if not hasattr(self, "state_pill"):
            return
        self.state_pill.configure(background=background)
        self.state_dot.configure(background=background, foreground=color)
        self.state_text.configure(
            background=background,
            foreground=color,
        )
        if state in {"working", "cancelling"}:
            if self.animation_job is None:
                self.animation_job = self.root.after(
                    80,
                    self._animate_visualizer,
                )
        else:
            self._stop_animation()
            self._draw_visualizer()

    def _on_links_modified(self, _event: tk.Event | None = None) -> None:
        if not self.url_input.edit_modified():
            return
        entries = parse_url_entries(self.url_input.get("1.0", "end-1c"))
        unique_count = len(dict.fromkeys(
            normalize_youtube_url(url) if is_youtube_url(url) else url
            for _line, url in entries
        ))
        self.link_count_var.set(str(unique_count))
        self.link_badge_var.set(
            f"{unique_count} {'link' if unique_count == 1 else 'links'}"
        )
        self.url_input.edit_modified(False)

    def _set_url_focus(self, focused: bool) -> None:
        self.url_border.configure(
            background=COLORS["primary"] if focused else COLORS["border"]
        )

    def _toggle_activity(self, visible: bool | None = None) -> None:
        if visible is None:
            visible = not self.activity_visible_var.get()
        self.activity_visible_var.set(visible)
        self._apply_activity_visibility()

    def _apply_activity_visibility(self) -> None:
        if not hasattr(self, "activity_card"):
            return
        if self.activity_visible_var.get():
            self.main.rowconfigure(5, minsize=70)
            self.activity_card.grid()
        else:
            self.main.rowconfigure(5, minsize=0)
            self.activity_card.grid_remove()

    def _paste_links(self) -> None:
        if self.running:
            return
        try:
            clipboard = self.root.clipboard_get()
        except tk.TclError:
            return
        if clipboard:
            self.url_input.insert("insert", clipboard)
            self.url_input.edit_modified(True)
            self._on_links_modified()
            self.url_input.focus_set()

    def _clear_links(self) -> None:
        if self.running:
            return
        self.url_input.delete("1.0", "end")
        self.url_input.edit_modified(True)
        self._on_links_modified()
        self.url_input.focus_set()

    def _select_all_links(self) -> None:
        if self.running:
            return
        self.url_input.tag_add("sel", "1.0", "end-1c")
        self.url_input.mark_set("insert", "1.0")
        self.url_input.see("insert")
        self.url_input.focus_set()

    def _new_session(self) -> None:
        if self.running:
            return
        self._clear_links()
        self._clear_log()
        self.saved_count = 0
        self.skipped_count = 0
        self.last_saved_path = None
        self._update_count()
        self.status_var.set("Ready to build your audio library")
        self._show_progress(0.0)
        self._set_visual_state("ready")

    def _show_about(self) -> None:
        messagebox.showinfo(
            APP_TITLE,
            "SEEK\n\n"
            "A colorful, resumable audio workspace for YouTube and "
            "Spotify links.\n\n"
            f"Version {APP_VERSION}",
        )

    def _show_shortcuts(self) -> None:
        messagebox.showinfo(
            APP_TITLE,
            "Keyboard shortcuts\n\n"
            "Ctrl+Enter   Start download\n"
            "Esc          Cancel active work\n"
            "Ctrl+L       Focus the link box\n"
            "Ctrl+O       Choose destination\n"
            "Ctrl+N       New session",
        )

    def _focus_download(self) -> None:
        self.url_input.focus_set()
        self.url_input.see("1.0")

    def _cancel_from_shortcut(self, _event: tk.Event) -> str:
        self._cancel_download()
        return "break"

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose output folder",
            initialdir=self.output_var.get() or str(Path.home()),
        )
        if selected:
            self.output_var.set(selected)


    def _open_general_settings(self) -> None:
        if self.running:
            return
            
        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.geometry("300x200")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        
        main_frame = tk.Frame(dialog, padx=20, pady=20, background=COLORS["surface"])
        main_frame.pack(fill="both", expand=True)
        
        tk.Label(main_frame, text="Audio Format:", background=COLORS["surface"], foreground=COLORS["text"]).pack(anchor="w")
        
        format_var = tk.StringVar(value=self.config.audio_format)
        formats = ["mp3", "m4a", "flac", "wav"]
        dropdown = ttk.Combobox(main_frame, textvariable=format_var, values=formats, state="readonly")
        dropdown.pack(fill="x", pady=(5, 15))
        
        tk.Label(main_frame, text="Bandwidth Limit:", background=COLORS["surface"], foreground=COLORS["text"]).pack(anchor="w")
        bw_var = tk.StringVar(value=self.config.bandwidth_limit)
        bw_formats = ["Unlimited", "1 MB/s", "5 MB/s", "10 MB/s", "25 MB/s"]
        bw_dropdown = ttk.Combobox(main_frame, textvariable=bw_var, values=bw_formats, state="readonly")
        bw_dropdown.pack(fill="x", pady=(5, 15))
        
        def save():
            self.config = DownloadConfig(
                audio_format=format_var.get(),
                audio_quality="192",
                bandwidth_limit=bw_var.get()
            )
            dialog.destroy()
            
        ttk.Button(main_frame, text="Save", command=save, style="Primary.TButton").pack(side="right")
        ttk.Button(main_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=10)

    def _open_spotify_settings(self) -> None:
        if self.running:
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Spotify settings")
        dialog.configure(background=COLORS["surface"])
        dialog.transient(self.root)
        dialog.resizable(False, False)

        existing_id, existing_secret = spotify.load_credentials() or ("", "")
        client_id_var = tk.StringVar(value=existing_id)
        client_secret_var = tk.StringVar(value=existing_secret)

        tk.Label(
            dialog,
            text=(
                "A single Spotify track link works with no setup.\n"
                "Albums and playlists need a free Client ID and Client "
                "Secret from\ndeveloper.spotify.com/dashboard — pasted "
                "below."
            ),
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            justify="left",
            font=("Segoe UI", 8),
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 4))

        tk.Label(
            dialog,
            text="Client ID",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            font=("Segoe UI", 9, "bold"),
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(10, 2))
        client_id_entry = ttk.Entry(
            dialog,
            textvariable=client_id_var,
            width=44,
            style="Modern.TEntry",
        )
        client_id_entry.grid(row=2, column=0, sticky="ew", padx=18)

        tk.Label(
            dialog,
            text="Client Secret",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            font=("Segoe UI", 9, "bold"),
        ).grid(row=3, column=0, sticky="w", padx=18, pady=(10, 2))
        client_secret_entry = ttk.Entry(
            dialog,
            textvariable=client_secret_var,
            width=44,
            show="•",
            style="Modern.TEntry",
        )
        client_secret_entry.grid(row=4, column=0, sticky="ew", padx=18)

        button_row = tk.Frame(dialog, background=COLORS["surface"])
        button_row.grid(row=5, column=0, sticky="e", padx=18, pady=18)

        def save(_event: tk.Event | None = None) -> None:
            client_id = client_id_var.get().strip()
            client_secret = client_secret_var.get().strip()
            if not client_id or not client_secret:
                messagebox.showerror(
                    APP_TITLE,
                    "Both the Client ID and Client Secret are required.",
                    parent=dialog,
                )
                return
            try:
                spotify.save_credentials(client_id, client_secret)
            except OSError as exc:
                messagebox.showerror(
                    APP_TITLE,
                    f"Could not save Spotify settings:\n{exc}",
                    parent=dialog,
                )
                return
            dialog.destroy()

        ttk.Button(
            button_row,
            text="Cancel",
            style="Secondary.TButton",
            command=dialog.destroy,
            cursor="hand2",
        ).pack(side="right")
        ttk.Button(
            button_row,
            text="Save",
            style="Primary.TButton",
            command=save,
            cursor="hand2",
        ).pack(side="right", padx=(0, 8))

        dialog.bind("<Return>", save)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        client_id_entry.focus_set()

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (
            (self.root.winfo_width() - dialog.winfo_width()) // 2
        )
        y = self.root.winfo_rooty() + (
            (self.root.winfo_height() - dialog.winfo_height()) // 2
        )
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()
        dialog.wait_window()

    def _start_download(self) -> None:
        if self.running:
            return

        entries = parse_url_entries(self.url_input.get("1.0", "end-1c"))
        urls = list(dict.fromkeys(
            normalize_youtube_url(url) if is_youtube_url(url) else url
            for _line_number, url in entries
        ))
        duplicate_count = len(entries) - len(urls)
        output_text = self.output_var.get().strip()

        if not entries:
            messagebox.showerror(
                APP_TITLE,
                "Paste at least one YouTube or Spotify link first.",
            )
            self.url_input.focus_set()
            return

        invalid_entries = [
            (line_number, url)
            for line_number, url in entries
            if not is_youtube_url(url) and not spotify.is_spotify_url(url)
        ]
        if invalid_entries:
            preview = "\n".join(
                f"Line {line_number}: {url}"
                for line_number, url in invalid_entries[:3]
            )
            if len(invalid_entries) > 3:
                preview += f"\n…and {len(invalid_entries) - 3} more"
            messagebox.showerror(
                APP_TITLE,
                f"These are not valid YouTube or Spotify links:\n\n{preview}",
            )
            self.url_input.focus_set()
            return
        if not output_text:
            messagebox.showerror(APP_TITLE, "Choose an output folder.")
            return

        output_dir = Path(output_text).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(
                APP_TITLE,
                f"Could not create the output folder:\n{exc}",
            )
            return

        self._clear_log()
        if duplicate_count:
            self._append_log(
                f"Skipped {duplicate_count} exact duplicate "
                f"{'link' if duplicate_count == 1 else 'links'}."
            )
        self._set_running(True)
        self.cancel_event.clear()
        self.saved_count = 0
        self.skipped_count = 0
        self.last_saved_path = None
        self._update_count()
        self.status_var.set("Preparing your queue…")
        self._show_progress(None)
        self._set_visual_state("working")

        self.worker = threading.Thread(
            target=self._download_worker,
            args=(urls, output_dir),
            daemon=True,
            name="youtube-download-worker",
        )
        self.worker.start()

    def _start_from_shortcut(self, _event: tk.Event) -> str:
        self._start_download()
        return "break"

    def _download_worker(
        self,
        urls: list[str],
        output_dir: Path,
    ) -> None:
        try:
            result = download_urls(
                urls,
                output_dir,
                self.events.put,
                self.cancel_event,
            )
            outcomes = []
            if result.completed_videos:
                outcomes.append(
                    f"{result.completed_videos} new "
                    f"{'video' if result.completed_videos == 1 else 'videos'} "
                    "saved"
                )
            if result.skipped_videos:
                outcomes.append(
                    f"{result.skipped_videos} already-complete "
                    f"{'video' if result.skipped_videos == 1 else 'videos'} "
                    "skipped"
                )
            outcome_text = "; ".join(outcomes)

            if result.had_errors and not outcomes:
                self.events.put(
                    DownloadEvent(
                        "job_failed",
                        "Download failed. No complete videos were saved. "
                        "See Activity for details.",
                    )
                )
            elif result.had_errors:
                message = (
                    "Finished with some unavailable or failed items. "
                    f"{outcome_text}."
                )
                self.events.put(DownloadEvent("job_partial", message))
            elif outcomes:
                message = f"Finished. {outcome_text}."
                self.events.put(DownloadEvent("job_complete", message, 100.0))
            else:
                self.events.put(
                    DownloadEvent(
                        "job_partial",
                        "No downloadable videos were found.",
                    )
                )
        except UserCancelledError:
            self.events.put(DownloadEvent("job_cancelled", "Download cancelled."))
        except MissingDependencyError as exc:
            self.events.put(DownloadEvent("job_failed", str(exc)))
        except DownloadFailedError as exc:
            self.events.put(DownloadEvent("job_failed", str(exc)))
        except Exception as exc:  # Defensive boundary for the worker thread.
            self.events.put(
                DownloadEvent("job_failed", f"Unexpected error: {exc}")
            )

    def _retry_failed(self) -> None:
        if self.running:
            return
        output_dir = Path(self.output_var.get())
        if not output_dir.exists():
            return
        try:
            from seek.core.journal import CompletionIndex
            index = CompletionIndex(output_dir, None)
            index.clear_failures()
        except Exception:
            pass
        self.retry_button.grid_remove()
        self._start_download()

    def _cancel_download(self) -> None:
        if not self.running:
            return
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.retry_button.configure(state="disabled")
        self.status_var.set("Cancelling…")
        self._set_visual_state("cancelling")
        self._append_log(
            "Cancellation requested. An active conversion may take a moment to stop."
        )

    def _drain_events(self) -> None:
        processed = 0
        try:
            while processed < 100:
                event = self.events.get_nowait()
                self._handle_event(event)
                processed += 1
        except queue.Empty:
            pass

        try:
            if self.root.winfo_exists():
                delay = 20 if not self.events.empty() else 100
                self.root.after(delay, self._drain_events)
        except tk.TclError:
            # The window may have been destroyed while handling a final event.
            pass

    def _handle_event(self, event: DownloadEvent) -> None:
        if event.kind == "progress":
            self.status_var.set(event.message)
            if self.visual_state != "cancelling":
                self._set_visual_state("working")
            self._show_progress(event.percent)
        elif event.kind == "processing":
            self.status_var.set(event.message)
            if self.visual_state != "cancelling":
                self._set_visual_state("working")
            self._show_progress(None)
        elif event.kind == "log":
            self._append_log(event.message)
        elif event.kind == "warning":
            self._append_log(self._prefixed_message("WARNING", event.message))
        elif event.kind == "error":
            self._append_log(self._prefixed_message("ERROR", event.message))
        elif event.kind == "video_complete":
            self.saved_count += 1
            self._update_count()
            self.last_saved_path = event.path
            self.status_var.set(event.message)
            self._append_log(event.message)
            self._show_progress(100.0)
        elif event.kind == "video_skipped":
            self.skipped_count += 1
            self._update_count()
            self.last_saved_path = event.path
            self.status_var.set(event.message)
            self._append_log(event.message)
        elif event.kind == "job_complete":
            self._finish_job(event.message, success=True)
        elif event.kind == "job_partial":
            self._finish_job(event.message, success=False, warning=True)
        elif event.kind == "job_cancelled":
            self._finish_job(event.message, success=False)
        elif event.kind == "job_failed":
            self._finish_job(event.message, success=False, error=True)

    def _update_count(self) -> None:
        saved = (
            f"{self.saved_count} "
            f"{'video' if self.saved_count == 1 else 'videos'} saved"
        )
        if self.skipped_count:
            saved += (
                f" • {self.skipped_count} already complete"
            )
        self.count_var.set(saved)
        if hasattr(self, "saved_stat_var"):
            self.saved_stat_var.set(str(self.saved_count))
        if hasattr(self, "skipped_stat_var"):
            self.skipped_stat_var.set(str(self.skipped_count))

    def _show_progress(self, percent: float | None) -> None:
        if percent is None:
            if not self.progress_is_indeterminate:
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
                self.progress_is_indeterminate = True
            return

        if self.progress_is_indeterminate:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress_is_indeterminate = False
        self.progress["value"] = percent

    def _finish_job(
        self,
        message: str,
        *,
        success: bool,
        warning: bool = False,
        error: bool = False,
    ) -> None:
        self._set_running(False)
        self._show_progress(100.0 if success else 0.0)
        self.status_var.set(message)
        if success:
            try:
                notification.notify(title="Download Complete", message=message, app_name="Seek")
            except Exception:
                pass
        self._append_log(message)
        if error or warning:
            self.retry_button.grid()
            self.retry_button.configure(state="normal")
        else:
            self.retry_button.grid_remove()
            
        if error:
            self._set_visual_state("failed")
        elif warning:
            self._set_visual_state("warning")
        elif success:
            self._set_visual_state("complete")
        else:
            self._set_visual_state("cancelled")

        if self.close_requested:
            self._stop_animation()
            self.root.destroy()
            return

        if error:
            messagebox.showerror(APP_TITLE, message)
        elif warning:
            messagebox.showwarning(APP_TITLE, message)
        elif success:
            messagebox.showinfo(APP_TITLE, message)

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.download_button.configure(
            state="disabled" if running else "normal"
        )
        self.cancel_button.configure(
            state="normal" if running else "disabled"
        )
        self.retry_button.configure(
            state="disabled" if running else "normal"
        )
        if running:
            self.retry_button.grid_remove()
        self.browse_button.configure(
            state="disabled" if running else "normal"
        )
        self.url_input.configure(
            state="disabled" if running else "normal"
        )
        self.output_entry.configure(
            state="disabled" if running else "normal"
        )
        self.paste_button.configure(
            state="disabled" if running else "normal"
        )
        self.clear_button.configure(
            state="disabled" if running else "normal"
        )
        self.download_menu.entryconfigure(
            "Start download",
            state="disabled" if running else "normal",
        )
        self.download_menu.entryconfigure(
            "Cancel",
            state="normal" if running else "disabled",
        )
        self.file_menu.entryconfigure(
            "New session",
            state="disabled" if running else "normal",
        )
        self.file_menu.entryconfigure(
            "Choose destination…",
            state="disabled" if running else "normal",
        )
        self.file_menu.entryconfigure(
            "Spotify settings…",
            state="disabled" if running else "normal",
        )
        for label in ("Paste links", "Select all links", "Clear links"):
            self.edit_menu.entryconfigure(
                label,
                state="disabled" if running else "normal",
            )

    def _append_log(self, message: str) -> None:
        if not message:
            return
        upper = message.upper()
        if upper.startswith("ERROR:"):
            tag = "error"
        elif upper.startswith("WARNING:"):
            tag = "warning"
        elif upper.startswith("SAVED") or upper.startswith("FINISHED"):
            tag = "success"
        elif upper.startswith("SKIPPED"):
            tag = "skip"
        else:
            tag = "info"
        self.log.configure(state="normal")
        self.log.insert("end", f"{message}\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    @staticmethod
    def _prefixed_message(prefix: str, message: str) -> str:
        if message.upper().startswith(f"{prefix}:"):
            return message
        return f"{prefix}: {message}"

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _open_output(self) -> None:
        path = Path(self.output_var.get()).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror(
                APP_TITLE,
                f"Could not open the output folder:\n{exc}",
            )

    def _on_close(self) -> None:
        if not self.running:
            self._stop_animation()
            self.root.destroy()
            return

        should_close = messagebox.askyesno(
            APP_TITLE,
            "A download is active. Cancel it and close when it stops?",
        )
        if should_close:
            self.close_requested = True
            self._cancel_download()


def main() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
    root = tk.Tk()
    YouTubeAudioApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
