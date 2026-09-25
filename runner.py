# runner.py
import os
import sys
import subprocess
import threading
import webbrowser
import logging
import platform
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

# Add scripts directory to path to locate the CLI's modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import paths
from tagpup import config as tagpup_config
from tagpup.core import library as libraries
from tagpup.core import suggesting

#: The one server for both pages (tagpup_web.py), and the ports its pages answer on.
SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tagpup_web.py")
PAGES = {"TagPup": 8090, "TagTuner": 8080}

class RunnerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TagpupCLI & TagTuner GUI Dashboard")
        self.root.geometry("1150x780")
        self.root.minsize(1000, 700)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Style colors
        self.BG_MAIN = "#1e1e1e"
        self.BG_PANEL = "#2d2d2d"
        self.BG_INPUT = "#3c3c3c"
        self.FG_MAIN = "#f1f1f1"
        self.FG_MUTED = "#8e8e8e"
        self.COLOR_ACCENT = "#007acc"
        self.COLOR_SUCCESS = "#2e7d32"
        self.COLOR_WARNING = "#d84315"
        self.COLOR_ERROR = "#c62828"
        self.FONT_MAIN = ("Segoe UI", 10)
        self.FONT_BOLD = ("Segoe UI", 10, "bold")
        self.FONT_TITLE = ("Segoe UI", 11, "bold")
        self.FONT_CONSOLE = ("Consolas", 10)

        self.root.configure(bg=self.BG_MAIN)

        # Process control state
        self.active_process = None
        
        # The web server, as a process of its own: what the launchers start.
        self.server_process = None

        # Build UI layout
        self.setup_ui()

        # Initialize Working Database Selector
        self.refresh_database_list()

        # Set up standard logging redirect
        self.setup_logging()

    def on_closing(self):
        # Stop the server if it is running
        self.stop_server_action()
        # Terminate any running subprocesses
        if self.active_process:
            try:
                if platform.system() == "Windows":
                    subprocess.call(["taskkill", "/F", "/T", "/PID", str(self.active_process.pid)])
                else:
                    self.active_process.terminate()
            except Exception:
                pass
        self.root.destroy()

    def setup_ui(self):
        # Two main columns: Left for controls, Right for logs
        self.root.grid_columnconfigure(0, weight=0, minsize=500)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        # Left Column Frame (Controls)
        left_canvas = tk.Canvas(self.root, bg=self.BG_MAIN, highlightthickness=0)
        scrollbar = tk.Scrollbar(self.root, orient="vertical", command=left_canvas.yview)
        self.left_frame = tk.Frame(left_canvas, bg=self.BG_MAIN, padx=10, pady=10)
        
        self.left_frame.bind(
            "<Configure>",
            lambda e: left_canvas.configure(scrollregion=left_canvas.bbox("all"))
        )
        
        left_canvas.create_window((0, 0), window=self.left_frame, anchor="nw")
        left_canvas.configure(yscrollcommand=scrollbar.set)
        
        left_canvas.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        scrollbar.grid(row=0, column=0, sticky="nse", padx=(0, 0), pady=10)

        # Right Column Frame (Logs)
        right_frame = tk.Frame(self.root, bg=self.BG_MAIN, padx=10, pady=10)
        right_frame.grid(row=0, column=1, sticky="nsew")
        right_frame.grid_columnconfigure(0, weight=1)
        right_frame.grid_rowconfigure(1, weight=1)

        # Right Frame Log Title
        log_title = tk.Label(right_frame, text="Terminal Output & Live Logs", font=self.FONT_TITLE, bg=self.BG_MAIN, fg=self.FG_MAIN, anchor="w")
        log_title.grid(row=0, column=0, sticky="ew", pady=(0, 5))

        # Log Scrolled Text Window
        self.console = scrolledtext.ScrolledText(
            right_frame,
            bg="#141414",
            fg="#dcdcdc",
            insertbackground="white",
            font=self.FONT_CONSOLE,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightcolor=self.COLOR_ACCENT,
            highlightbackground="#333333"
        )
        self.console.grid(row=1, column=0, sticky="nsew")

        # Tags for colored console log output
        self.console.tag_config("normal", foreground="#dcdcdc")
        self.console.tag_config("info", foreground="#61afef")
        self.console.tag_config("success", foreground="#98c379")
        self.console.tag_config("warning", foreground="#e5c07b")
        self.console.tag_config("error", foreground="#e06c75")

        # Bottom actions / status for console
        console_bar = tk.Frame(right_frame, bg=self.BG_MAIN)
        console_bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        console_bar.grid_columnconfigure(0, weight=1)

        self.status_label = tk.Label(console_bar, text="Status: Ready", font=self.FONT_MAIN, bg=self.BG_MAIN, fg=self.FG_MUTED, anchor="w")
        self.status_label.grid(row=0, column=0, sticky="w")

        self.btn_cancel = tk.Button(
            console_bar,
            text="Cancel Running Process",
            bg=self.COLOR_ERROR,
            fg="white",
            activebackground="#b71c1c",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=5,
            font=self.FONT_BOLD,
            command=self.cancel_process,
            state=tk.DISABLED
        )
        self.btn_cancel.grid(row=0, column=1, sticky="e", padx=(0, 10))

        btn_clear = tk.Button(
            console_bar,
            text="Clear Log",
            bg="#3a3a3a",
            fg="white",
            activebackground="#4a4a4a",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=15,
            pady=5,
            font=self.FONT_BOLD,
            command=self.clear_log
        )
        btn_clear.grid(row=0, column=2, sticky="e")

        # --- PANELS IN LEFT COLUMN CONTROLS ---

        # 1. Global Setup Panel
        p_global = self.create_panel("Global Settings")
        
        self.var_test_db = tk.BooleanVar(value=True)
        chk_test = tk.Checkbutton(
            p_global,
            text="Use Isolated Test Mode Database (test_photo_index.db)",
            variable=self.var_test_db,
            bg=self.BG_PANEL,
            fg=self.FG_MAIN,
            selectcolor="#1a1a1a",
            activebackground=self.BG_PANEL,
            activeforeground=self.FG_MAIN,
            font=self.FONT_MAIN,
            command=self.update_suggestions_path
        )
        chk_test.pack(anchor="w", pady=5)

        db_frame = tk.Frame(p_global, bg=self.BG_PANEL)
        db_frame.pack(anchor="w", fill="x", pady=5)
        
        lbl_db = tk.Label(
            db_frame, 
            text="Working Database:", 
            bg=self.BG_PANEL, 
            fg=self.FG_MAIN,
            font=self.FONT_MAIN
        )
        lbl_db.pack(side="left", padx=(0, 5))
        
        self.combo_db = ttk.Combobox(db_frame, state="readonly", width=30)
        self.combo_db.pack(side="left", padx=5)
        self.combo_db.bind("<<ComboboxSelected>>", self.on_database_selected)
        
        btn_refresh = tk.Button(
            db_frame,
            text="🔄",
            bg="#3c3c3c",
            fg="white",
            relief=tk.FLAT,
            bd=0,
            padx=5,
            command=self.refresh_database_list
        )
        btn_refresh.pack(side="left", padx=5)

        # 2. Web Server Panel: one server, both pages
        p_server = self.create_panel("1. Web Server (TagPup and TagTuner)")

        server_btn_frame = tk.Frame(p_server, bg=self.BG_PANEL)
        server_btn_frame.pack(fill="x", pady=5)

        self.btn_start_server = tk.Button(
            server_btn_frame,
            text="Start Server",
            bg=self.COLOR_SUCCESS,
            fg="white",
            activebackground="#1b5e20",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            font=self.FONT_BOLD,
            command=self.start_server_action
        )
        self.btn_start_server.pack(side="left", padx=(0, 10))

        self.btn_stop_server = tk.Button(
            server_btn_frame,
            text="Stop Server",
            bg=self.COLOR_ERROR,
            fg="white",
            activebackground="#b71c1c",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            font=self.FONT_BOLD,
            command=self.stop_server_action,
            state=tk.DISABLED
        )
        self.btn_stop_server.pack(side="left", padx=(0, 10))

        self.btn_open_pages = {}
        for name in PAGES:
            button = tk.Button(
                server_btn_frame,
                text="Open %s" % name,
                bg=self.COLOR_ACCENT,
                fg="white",
                activebackground="#0062a3",
                relief=tk.FLAT,
                bd=0,
                padx=12,
                pady=6,
                font=self.FONT_BOLD,
                command=lambda name=name: self.open_page_action(name),
                state=tk.DISABLED
            )
            button.pack(side="left", padx=(0, 10))
            self.btn_open_pages[name] = button

        self.lbl_server_status = tk.Label(p_server, text="Status: Server Stopped", bg=self.BG_PANEL, fg=self.FG_MUTED, font=self.FONT_MAIN)
        self.lbl_server_status.pack(anchor="w", pady=(5, 0))

        # 3. Indexing Panel
        p_index = self.create_panel("2. Scans, Indexing & Face Detection")
        
        lbl_dir = tk.Label(p_index, text="Photo Directory to Index:", bg=self.BG_PANEL, fg=self.FG_MAIN, font=self.FONT_MAIN)
        lbl_dir.pack(anchor="w", pady=(0, 2))

        dir_frame = tk.Frame(p_index, bg=self.BG_PANEL)
        dir_frame.pack(fill="x", pady=(0, 8))

        self.ent_index_dir = tk.Entry(dir_frame, bg=self.BG_INPUT, fg="white", insertbackground="white", relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground="#555555", font=self.FONT_MAIN)
        self.ent_index_dir.pack(side="left", fill="x", expand=True, ipady=4, ipadx=4)
        
        btn_browse_index = tk.Button(
            dir_frame,
            text="Browse",
            bg="#444444",
            fg="white",
            activebackground="#555555",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            font=self.FONT_MAIN,
            command=lambda: self.browse_directory(self.ent_index_dir)
        )
        btn_browse_index.pack(side="right", padx=(5, 0))

        # Checkboxes for Index options
        self.var_reset_index = tk.BooleanVar()
        self.var_force_reembed = tk.BooleanVar()
        self.var_skip_faces = tk.BooleanVar()

        chk_reset_idx = tk.Checkbutton(p_index, text="Reset Index completely (--reset)", variable=self.var_reset_index, bg=self.BG_PANEL, fg=self.FG_MAIN, selectcolor="#1a1a1a", activebackground=self.BG_PANEL, font=self.FONT_MAIN)
        chk_reset_idx.pack(anchor="w")
        
        chk_force_emb = tk.Checkbutton(p_index, text="Force re-compute embeddings (--force-reembed)", variable=self.var_force_reembed, bg=self.BG_PANEL, fg=self.FG_MAIN, selectcolor="#1a1a1a", activebackground=self.BG_PANEL, font=self.FONT_MAIN)
        chk_force_emb.pack(anchor="w")

        chk_skip_faces = tk.Checkbutton(p_index, text="Skip face detection during indexing (--skip-faces)", variable=self.var_skip_faces, bg=self.BG_PANEL, fg=self.FG_MAIN, selectcolor="#1a1a1a", activebackground=self.BG_PANEL, font=self.FONT_MAIN)
        chk_skip_faces.pack(anchor="w", pady=(0, 8))

        idx_btn_frame = tk.Frame(p_index, bg=self.BG_PANEL)
        idx_btn_frame.pack(fill="x")

        self.btn_run_indexing = tk.Button(
            idx_btn_frame,
            text="Run Photo Indexer",
            bg=self.COLOR_ACCENT,
            fg="white",
            activebackground="#0062a3",
            relief=tk.FLAT,
            bd=0,
            padx=15,
            pady=6,
            font=self.FONT_BOLD,
            command=self.run_indexing_action
        )
        self.btn_run_indexing.pack(side="left", padx=(0, 10))

        self.btn_run_faces = tk.Button(
            idx_btn_frame,
            text="Extract & Index Faces",
            bg="#555555",
            fg="white",
            activebackground="#666666",
            relief=tk.FLAT,
            bd=0,
            padx=15,
            pady=6,
            font=self.FONT_BOLD,
            command=self.run_faces_action
        )
        self.btn_run_faces.pack(side="left")

        # 4. Identity Clustering Panel
        p_cluster = self.create_panel("3. Face Clustering")
        
        self.var_reset_cluster = tk.BooleanVar()
        chk_reset_cls = tk.Checkbutton(p_cluster, text="Reset previous assignments (--reset)", variable=self.var_reset_cluster, bg=self.BG_PANEL, fg=self.FG_MAIN, selectcolor="#1a1a1a", activebackground=self.BG_PANEL, font=self.FONT_MAIN)
        chk_reset_cls.pack(anchor="w", pady=(0, 5))

        iters_frame = tk.Frame(p_cluster, bg=self.BG_PANEL)
        iters_frame.pack(fill="x", pady=(0, 8))
        
        lbl_iters = tk.Label(iters_frame, text="Max Propagation Iterations:", bg=self.BG_PANEL, fg=self.FG_MAIN, font=self.FONT_MAIN)
        lbl_iters.pack(side="left")

        self.ent_max_iters = tk.Entry(iters_frame, bg=self.BG_INPUT, fg="white", insertbackground="white", width=6, relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground="#555555", justify="center", font=self.FONT_MAIN)
        self.ent_max_iters.pack(side="left", padx=(8, 0))
        self.ent_max_iters.insert(0, "5")

        self.btn_run_clustering = tk.Button(
            p_cluster,
            text="Run Identity Resolution Clustering",
            bg=self.COLOR_ACCENT,
            fg="white",
            activebackground="#0062a3",
            relief=tk.FLAT,
            bd=0,
            padx=15,
            pady=6,
            font=self.FONT_BOLD,
            command=self.run_clustering_action
        )
        self.btn_run_clustering.pack(anchor="w")

        # 5. Suggestions & ExifTool Writing Panel
        p_suggest = self.create_panel("4. AI Tag Recommendations & ExifTool Writes")
        
        lbl_target = tk.Label(p_suggest, text="Target Photo Directory for suggestions:", bg=self.BG_PANEL, fg=self.FG_MAIN, font=self.FONT_MAIN)
        lbl_target.pack(anchor="w", pady=(0, 2))

        target_frame = tk.Frame(p_suggest, bg=self.BG_PANEL)
        target_frame.pack(fill="x", pady=(0, 8))

        self.ent_target_dir = tk.Entry(target_frame, bg=self.BG_INPUT, fg="white", insertbackground="white", relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground="#555555", font=self.FONT_MAIN)
        self.ent_target_dir.pack(side="left", fill="x", expand=True, ipady=4, ipadx=4)
        
        btn_browse_target = tk.Button(
            target_frame,
            text="Browse",
            bg="#444444",
            fg="white",
            activebackground="#555555",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            font=self.FONT_MAIN,
            command=lambda: self.browse_directory(self.ent_target_dir)
        )
        btn_browse_target.pack(side="right", padx=(5, 0))

        # Parameters grid
        params_frame = tk.Frame(p_suggest, bg=self.BG_PANEL)
        params_frame.pack(fill="x", pady=(0, 8))
        params_frame.grid_columnconfigure(1, weight=1)
        params_frame.grid_columnconfigure(3, weight=1)

        lbl_sim = tk.Label(params_frame, text="Min Cosine Sim:", bg=self.BG_PANEL, fg=self.FG_MAIN, font=self.FONT_MAIN)
        lbl_sim.grid(row=0, column=0, sticky="w", pady=2)
        
        self.ent_min_sim = tk.Entry(params_frame, bg=self.BG_INPUT, fg="white", insertbackground="white", width=8, relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground="#555555", justify="center", font=self.FONT_MAIN)
        self.ent_min_sim.grid(row=0, column=1, sticky="w", padx=(5, 10), pady=2)
        self.ent_min_sim.insert(0, "0.35")

        lbl_k = tk.Label(params_frame, text="K Neighbors:", bg=self.BG_PANEL, fg=self.FG_MAIN, font=self.FONT_MAIN)
        lbl_k.grid(row=0, column=2, sticky="w", pady=2)

        self.ent_k = tk.Entry(params_frame, bg=self.BG_INPUT, fg="white", insertbackground="white", width=8, relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground="#555555", justify="center", font=self.FONT_MAIN)
        self.ent_k.grid(row=0, column=3, sticky="w", padx=(5, 0), pady=2)
        self.ent_k.insert(0, "15")

        # Suggestions File Path Row
        lbl_file = tk.Label(p_suggest, text="Suggestions File Output:", bg=self.BG_PANEL, fg=self.FG_MAIN, font=self.FONT_MAIN)
        lbl_file.pack(anchor="w", pady=(0, 2))

        self.ent_suggest_file = tk.Entry(p_suggest, bg=self.BG_INPUT, fg="white", insertbackground="white", relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground="#555555", font=self.FONT_MAIN)
        self.ent_suggest_file.pack(fill="x", pady=(0, 8), ipady=4)
        self.ent_suggest_file.insert(0, "test_suggestions.json") # Init based on test_db default

        # Write options (nobackup & MinScore)
        write_opt_frame = tk.Frame(p_suggest, bg=self.BG_PANEL)
        write_opt_frame.pack(fill="x", pady=(0, 8))

        lbl_min_score = tk.Label(write_opt_frame, text="Write Min Score Threshold:", bg=self.BG_PANEL, fg=self.FG_MAIN, font=self.FONT_MAIN)
        lbl_min_score.pack(side="left")

        self.ent_min_score = tk.Entry(write_opt_frame, bg=self.BG_INPUT, fg="white", insertbackground="white", width=8, relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground="#555555", justify="center", font=self.FONT_MAIN)
        self.ent_min_score.pack(side="left", padx=(8, 15))
        self.ent_min_score.insert(0, "%.2f" % suggesting.OFFER_A_TAG)

        self.var_nobackup = tk.BooleanVar(value=True)
        chk_nobackup = tk.Checkbutton(
            write_opt_frame, 
            text="NoBackup (--nobackup)", 
            variable=self.var_nobackup, 
            bg=self.BG_PANEL, 
            fg=self.FG_MAIN, 
            selectcolor="#1a1a1a", 
            activebackground=self.BG_PANEL, 
            font=self.FONT_MAIN
        )
        chk_nobackup.pack(side="left")

        # Suggest / Write Actions Row
        sugg_btn_frame = tk.Frame(p_suggest, bg=self.BG_PANEL)
        sugg_btn_frame.pack(fill="x")

        self.btn_run_suggest = tk.Button(
            sugg_btn_frame,
            text="Generate AI Suggestions",
            bg=self.COLOR_ACCENT,
            fg="white",
            activebackground="#0062a3",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            font=self.FONT_BOLD,
            command=self.run_suggest_action
        )
        self.btn_run_suggest.pack(side="left", padx=(0, 10))

        self.btn_write_preview = tk.Button(
            sugg_btn_frame,
            text="Write Tags (Preview)",
            bg="#555555",
            fg="white",
            activebackground="#666666",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            font=self.FONT_BOLD,
            command=self.run_write_preview_action
        )
        self.btn_write_preview.pack(side="left", padx=(0, 10))

        self.btn_write_live = tk.Button(
            sugg_btn_frame,
            text="Write Tags (LIVE)",
            bg=self.COLOR_SUCCESS,
            fg="white",
            activebackground="#1b5e20",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            font=self.FONT_BOLD,
            command=self.run_write_live_action
        )
        self.btn_write_live.pack(side="left")

    def create_panel(self, title):
        # Helper to create styled frames
        panel = tk.Frame(self.left_frame, bg=self.BG_PANEL, relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground="#3f3f3f", padx=12, pady=12)
        panel.pack(fill="x", pady=(0, 15))
        
        lbl_title = tk.Label(panel, text=title, font=self.FONT_TITLE, bg=self.BG_PANEL, fg=self.COLOR_ACCENT, anchor="w")
        lbl_title.pack(fill="x", pady=(0, 2))
        
        # Horizontal line
        line = tk.Frame(panel, height=1, bg="#444444")
        line.pack(fill="x", pady=(0, 10))
        
        return panel

    def setup_logging(self):
        # Redirect python logger logs to GUI console
        class GuiLogHandler(logging.Handler):
            def __init__(self, app):
                super().__init__()
                self.app = app
            def emit(self, record):
                msg = self.format(record) + "\n"
                self.app.root.after(0, lambda: self.app.log_text(msg, tag="normal"))

        handler = GuiLogHandler(self)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s"))
        logging.getLogger("tagtuner.server").addHandler(handler)
        logging.getLogger("tagtuner.server").setLevel(logging.INFO)

    def log_text(self, text, tag="normal"):
        self.console.config(state=tk.NORMAL)
        
        # Check text contents for warning/error keywords to apply colors dynamically
        assigned_tag = tag
        if tag == "normal":
            lower_text = text.lower()
            if "error" in lower_text or "failed" in lower_text or "exception" in lower_text:
                assigned_tag = "error"
            elif "warning" in lower_text or "warn" in lower_text:
                assigned_tag = "warning"
            elif "success" in lower_text or "completed successfully" in lower_text or "ok" in lower_text:
                assigned_tag = "success"

        self.console.insert(tk.END, text, assigned_tag)
        self.console.see(tk.END)
        self.console.config(state=tk.DISABLED)

    def clear_log(self):
        self.console.config(state=tk.NORMAL)
        self.console.delete("1.0", tk.END)
        self.console.config(state=tk.DISABLED)

    def update_suggestions_path(self):
        # Update path name depending on whether test database is selected
        self.ent_suggest_file.delete(0, tk.END)
        if self.var_test_db.get():
            self.ent_suggest_file.insert(0, "test_suggestions.json")
        else:
            self.ent_suggest_file.insert(0, "suggestions.json")
        self.sync_test_mode_db()
        self.refresh_database_list()

    def refresh_database_list(self):
        settings = tagpup_config.load()
        data_dir = tagpup_config.data_dir(settings)
        test_mode = self.var_test_db.get()

        files = os.listdir(data_dir) if os.path.exists(data_dir) else []
        databases = libraries.picker_names(files, test_mode)
        self.combo_db["values"] = databases
        if self.combo_db.get() in databases:
            return
        self.combo_db.set(databases[0] if databases else "")

    def on_database_selected(self, event=None):
        # The choice lives in this window: the CLI commands below are given it, and
        # the server is started on it. Nothing writes it to config.ini (#100).
        selected_name = self.combo_db.get()
        if selected_name:
            self.log_text(f"Working database: {selected_name}\n", tag="info")

    def browse_directory(self, entry_widget):
        selected_dir = filedialog.askdirectory(initialdir=os.getcwd())
        if selected_dir:
            # The stored spelling, so the folder handed to the CLI walks to paths
            # the index already knows (the dialog answers with forward slashes).
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, paths.stored(selected_dir))

    def set_controls_state(self, state):
        # Disable or enable interactive buttons when task is running
        for btn in [
            self.btn_run_indexing, self.btn_run_faces, self.btn_run_clustering,
            self.btn_run_suggest, self.btn_write_preview, self.btn_write_live
        ]:
            btn.config(state=state)
        
        if state == "disabled":
            self.btn_cancel.config(state=tk.NORMAL)
        else:
            self.btn_cancel.config(state=tk.DISABLED)

    def cancel_process(self):
        if self.active_process:
            try:
                # Terminate running subprocess
                if platform.system() == "Windows":
                    # Use taskkill to kill process tree cleanly on Windows
                    subprocess.call(["taskkill", "/F", "/T", "/PID", str(self.active_process.pid)])
                else:
                    self.active_process.terminate()
                self.log_text("\n>>> Process termination requested by user.\n", tag="warning")
            except Exception as e:
                self.log_text(f"\n>>> Error canceling process: {e}\n", tag="error")

    # --- PROCESS RUNNING HELPER ---
    def execute_command(self, cmd_args, auto_confirm_yes=False):
        self.set_controls_state("disabled")
        self.status_label.config(text=f"Status: Running {' '.join(cmd_args[:3])}...")
        self.log_text(f"\n>>> Executing: {' '.join(cmd_args)}\n", tag="info")

        def worker():
            try:
                creationflags = 0
                if platform.system() == "Windows":
                    creationflags = subprocess.CREATE_NO_WINDOW

                self.active_process = subprocess.Popen(
                    cmd_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                    bufsize=-1,
                    creationflags=creationflags
                )

                if auto_confirm_yes:
                    # Write YES to stdin immediately and close the pipe
                    self.active_process.stdin.write(b"YES\n")
                    self.active_process.stdin.flush()
                    self.active_process.stdin.close()

                # Read process output line by line in real time
                for line in iter(self.active_process.stdout.readline, b""):
                    decoded_line = line.decode("utf-8", errors="replace")
                    self.root.after(0, lambda text=decoded_line: self.log_text(text))

                self.active_process.stdout.close()
                return_code = self.active_process.wait()

                if return_code == 0:
                    self.root.after(0, lambda: self.log_text("\n>>> Action completed successfully.\n", tag="success"))
                    self.root.after(0, lambda: self.status_label.config(text="Status: Action Completed"))
                else:
                    self.root.after(0, lambda: self.log_text(f"\n>>> Command failed with code {return_code}.\n", tag="error"))
                    self.root.after(0, lambda: self.status_label.config(text=f"Status: Failed (Code {return_code})"))

            except Exception as err:
                # Bound as a default argument: Python deletes the `except` variable when
                # the block ends, and this lambda runs later on the UI thread -- so the
                # error handler itself raised NameError, every time it fired.
                self.root.after(0, lambda message=str(err): self.log_text(
                    f"\n>>> Execution error: {message}\n", tag="error"))
                self.root.after(0, lambda: self.status_label.config(text="Status: Execution Error"))
            finally:
                self.active_process = None
                self.root.after(0, lambda: self.set_controls_state("normal"))

        threading.Thread(target=worker, daemon=True).start()

    # --- ACTIONS ---

    def server_url(self, name):
        return "http://localhost:%d/" % PAGES[name]

    def start_server_action(self):
        if self.server_process and self.server_process.poll() is None:
            return

        cmd = [sys.executable, SERVER]
        db_val = self.combo_db.get()
        if db_val:
            # The server serves this library to a URL naming none, so the pages
            # opened from here start on the working database.
            cmd.extend(["--db", libraries.for_mode(db_val + ".db", self.var_test_db.get())])
        self.log_text("Starting the web server: %s\n" % " ".join(cmd[1:]), tag="info")
        try:
            self.server_process = subprocess.Popen(cmd, cwd=os.path.dirname(SERVER))
        except Exception as e:
            self.log_text(f"Failed to start the server: {e}\n", tag="error")
            self.server_process = None
            return

        self.btn_start_server.config(state=tk.DISABLED)
        self.btn_stop_server.config(state=tk.NORMAL)
        for button in self.btn_open_pages.values():
            button.config(state=tk.NORMAL)
        self.lbl_server_status.config(
            text="Status: TagPup on port %d, TagTuner on port %d" % (PAGES["TagPup"], PAGES["TagTuner"]),
            fg="#a3e635")
        self.log_text("The server is starting; its log is in data/logs/tagpup_web.log.\n", tag="success")
        self.root.after(2000, self.check_server_alive)

    def check_server_alive(self):
        """A server that exited straight away -- a port in use, say -- is reported
        rather than shown as running."""
        if self.server_process and self.server_process.poll() is not None:
            self.log_text("The server exited (code %s); see data/logs/tagpup_web.log.\n"
                          % self.server_process.returncode, tag="error")
            self.server_stopped()

    def stop_server_action(self):
        process = self.server_process
        if not process:
            return
        self.server_process = None
        if process.poll() is None:
            try:
                if platform.system() == "Windows":
                    subprocess.call(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    process.terminate()
            except Exception as e:
                self.log_text(f"Error stopping the server: {e}\n", tag="error")
        self.server_stopped()

    def server_stopped(self):
        self.server_process = None
        if not hasattr(self, "btn_start_server"):
            return   # closing before the panels were built
        self.btn_start_server.config(state=tk.NORMAL)
        self.btn_stop_server.config(state=tk.DISABLED)
        for button in self.btn_open_pages.values():
            button.config(state=tk.DISABLED)
        self.lbl_server_status.config(text="Status: Server Stopped", fg=self.FG_MUTED)
        self.log_text("The web server has been stopped.\n", tag="warning")

    def open_page_action(self, name):
        if self.server_process and self.server_process.poll() is None:
            webbrowser.open(self.server_url(name))

    def run_indexing_action(self):
        directory = self.ent_index_dir.get().strip()
        if not directory:
            messagebox.showerror("Error", "Please select a photo directory to index.")
            return
        if not os.path.exists(directory):
            messagebox.showerror("Error", f"Selected directory does not exist:\n{directory}")
            return

        cmd = [sys.executable, "tagpup_cli.py"]
        if self.var_test_db.get():
            cmd.append("--test")
        db_val = self.combo_db.get()
        if db_val:
            db_name = libraries.for_mode(db_val + ".db", self.var_test_db.get())
            cmd.extend(["--db", db_name])
            
        cmd.extend(["index", directory])

        if self.var_reset_index.get():
            cmd.append("--reset")
        if self.var_force_reembed.get():
            cmd.append("--force-reembed")
        if self.var_skip_faces.get():
            cmd.append("--skip-faces")

        self.execute_command(cmd)

    def run_faces_action(self):
        directory = self.ent_index_dir.get().strip()
        if not directory:
            messagebox.showerror("Error", "Please select a photo directory to index faces from.")
            return
        if not os.path.exists(directory):
            messagebox.showerror("Error", f"Selected directory does not exist:\n{directory}")
            return

        cmd = [sys.executable, "tagpup_cli.py"]
        if self.var_test_db.get():
            cmd.append("--test")
        db_val = self.combo_db.get()
        if db_val:
            db_name = libraries.for_mode(db_val + ".db", self.var_test_db.get())
            cmd.extend(["--db", db_name])
            
        cmd.extend(["index-faces", directory])

        self.execute_command(cmd)

    def run_clustering_action(self):
        cmd = [sys.executable, "tagpup_cli.py"]
        if self.var_test_db.get():
            cmd.append("--test")
        db_val = self.combo_db.get()
        if db_val:
            db_name = libraries.for_mode(db_val + ".db", self.var_test_db.get())
            cmd.extend(["--db", db_name])
            
        cmd.append("cluster-faces")

        if self.var_reset_cluster.get():
            cmd.append("--reset")

        max_iters = self.ent_max_iters.get().strip()
        if max_iters:
            try:
                int(max_iters)
                cmd.extend(["--max-iterations", max_iters])
            except ValueError:
                pass

        self.execute_command(cmd)

    def run_suggest_action(self):
        directory = self.ent_target_dir.get().strip()
        output_file = self.ent_suggest_file.get().strip()

        if not directory:
            messagebox.showerror("Error", "Please select a target directory for suggestions.")
            return
        if not os.path.exists(directory):
            messagebox.showerror("Error", f"Target directory does not exist:\n{directory}")
            return
        if not output_file:
            messagebox.showerror("Error", "Please specify a suggestions file output path.")
            return

        cmd = [sys.executable, "tagpup_cli.py"]
        if self.var_test_db.get():
            cmd.append("--test")
        db_val = self.combo_db.get()
        if db_val:
            db_name = libraries.for_mode(db_val + ".db", self.var_test_db.get())
            cmd.extend(["--db", db_name])
            
        cmd.extend(["suggest", directory, "--output", output_file])

        # Add parameters
        min_sim = self.ent_min_sim.get().strip()
        if min_sim:
            cmd.extend(["--min-sim", min_sim])
        k = self.ent_k.get().strip()
        if k:
            cmd.extend(["--k", k])

        self.execute_command(cmd)

    def run_write_preview_action(self):
        suggestions_file = self.ent_suggest_file.get().strip()
        if not suggestions_file:
            messagebox.showerror("Error", "Please specify a suggestions file.")
            return
        if not os.path.exists(suggestions_file):
            messagebox.showerror("Error", f"Suggestions file not found:\n{suggestions_file}")
            return

        cmd = [sys.executable, "tagpup_cli.py", "write", suggestions_file]

        min_score = self.ent_min_score.get().strip()
        if min_score:
            cmd.extend(["-MinScore", min_score])
        if self.var_nobackup.get():
            cmd.append("--nobackup")

        self.execute_command(cmd)

    def run_write_live_action(self):
        suggestions_file = self.ent_suggest_file.get().strip()
        if not suggestions_file:
            messagebox.showerror("Error", "Please specify a suggestions file.")
            return
        if not os.path.exists(suggestions_file):
            messagebox.showerror("Error", f"Suggestions file not found:\n{suggestions_file}")
            return

        # Show GUI dialog warning before modifying files
        confirm = messagebox.askyesno(
            "Confirm LIVE Write",
            "Warning: You are about to perform a LIVE write operation. "
            "This will modify your photo files directly with the suggested tags.\n\n"
            "Are you sure you want to write tags directly to files?"
        )
        if not confirm:
            self.log_text("LIVE write operation cancelled by user.\n", tag="warning")
            return

        cmd = [sys.executable, "tagpup_cli.py", "write", suggestions_file, "-Live"]

        min_score = self.ent_min_score.get().strip()
        if min_score:
            cmd.extend(["-MinScore", min_score])
        if self.var_nobackup.get():
            cmd.append("--nobackup")

        # Start execution with auto confirmation feeding
        self.execute_command(cmd, auto_confirm_yes=True)

if __name__ == "__main__":
    from tagpup import logs as tagpup_logs
    logging.getLogger("runner").info("Logging to %s", tagpup_logs.to_file("runner"))
    root = tk.Tk()
    app = RunnerApp(root)
    root.mainloop()
