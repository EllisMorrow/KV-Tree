import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import queue
import sys
import webbrowser

from src.ui.tray_icon import AppTrayIcon
from src.logic.path_filter import normalize
from src.logic.file_monitor import _is_within_folder
from src.ui.components import BlacklistWindow, DynamicListWindow, ScrollablePane, ToolTip

try:
    import winreg
except ImportError:
    winreg = None

class KvTreeAppUI(tk.Tk):
    VERSION = "1.3.2"

    def __init__(self, app_state, task_dispatcher, file_monitor):
        super().__init__()
        self.app_state = app_state
        self.dispatcher = task_dispatcher
        self.file_monitor = file_monitor
        self._output_sources = {}
        self._ui_callback_queue = queue.Queue()
        self.after(25, self._drain_ui_callbacks)
        
        # The dispatcher and file monitor use worker threads.  Marshal every
        # UI callback through Tk's event loop, including dialogs.
        self.dispatcher.ui_cb = {
            'set_status': self._on_ui_thread(self.set_status),
            'update_progress': self._on_ui_thread(self.update_progress),
            'update_lists': self._prepare_list_refresh,
            'folder_scanned': self._on_ui_thread(self._show_scan_results_and_add),
            'exports_deleted': self._on_ui_thread(self._show_export_deletion_result),
            'show_error': self._on_ui_thread(self.show_error)
        }
        self.file_monitor.ui_cb = self.dispatcher.ui_cb

        self.title(f"KVTree - v{self.VERSION} (Official)")
        self.state("normal")
        
        saved_geo = self.app_state.get_window_geometry()
        if saved_geo:
            try:
                self.geometry(saved_geo)
            except tk.TclError:
                saved_geo = ""
        if not saved_geo:
            width = min(1080, self.winfo_screenwidth() - 40)
            height = min(800, self.winfo_screenheight() - 100)
            x = (self.winfo_screenwidth() - width) // 2
            y = (self.winfo_screenheight() - height) // 2
            self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(780, 560)

        icon_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", "..", "icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(default=icon_path)
                # 使用 PIL 加载图标并通过 wm_iconphoto 设置任务栏图标
                from PIL import Image, ImageTk
                icon_image = Image.open(icon_path)
                self._icon_photo = ImageTk.PhotoImage(icon_image)
                self.wm_iconphoto(True, self._icon_photo)
            except Exception:
                pass

        opts = self.app_state.get_advanced_options()
        self.auto_generate = tk.BooleanVar(value=opts.get("auto_generate", True))
        
        self.tray_icon = AppTrayIcon(self, self.VERSION)
        
        self.theme_var = tk.StringVar(
            value="dark" if self.app_state.get_advanced_options().get("theme") == "dark" else "light"
        )
        self._apply_theme()
        self.build_ui()
        self.load_state_to_ui()

        self.tray_icon.setup()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.bind("<Unmap>", self.on_minimize)

        self.dispatcher.start()
        self.dispatcher.put_task(("initialize",))
        if self.auto_generate.get():
            self.file_monitor.start()
        self.after(1000, self._check_file_monitor)

    def _check_file_monitor(self):
        try:
            self.file_monitor.check_watches()
        except OSError as error:
            self.set_status(f"文件监控恢复失败，将重试：{error}")
        self.after(1000, self._check_file_monitor)

    def _apply_theme(self):
        dark = self.theme_var.get() == "dark"
        style = ttk.Style(self)
        style.theme_use("clam")
        if dark:
            page, surface, field, stripe = "#141b27", "#1e2939", "#192333", "#202c3e"
            text, muted, line = "#eef2fa", "#acb9cc", "#344358"
            accent, hover, selected = "#93b8ff", "#afcaff", "#304b77"
            accent_text, warning, danger = "#14213b", "#ffce83", "#ffb3b2"
        else:
            page, surface, field, stripe = "#f1f5fa", "#ffffff", "#ffffff", "#f5f8fd"
            text, muted, line = "#17263b", "#53667d", "#d6e0ec"
            accent, hover, selected = "#2458ba", "#184aab", "#dce9ff"
            accent_text, warning, danger = "#ffffff", "#855209", "#a12c3a"

        font = ("Microsoft YaHei UI", 10)
        bold = (*font, "bold")
        row_height = max(29, round(self.winfo_fpixels("1p") * 21))
        style.configure(".", font=font, background=surface, foreground=text,
                        fieldbackground=field, bordercolor=line, lightcolor=surface,
                        darkcolor=surface, troughcolor=page, arrowcolor=muted,
                        selectbackground=selected, selectforeground=text, insertcolor=text)
        style.configure("Page.TFrame", background=page)
        style.configure("Brand.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Muted.TLabel", foreground=muted)
        style.configure("Monitor.TLabel", foreground=accent, background=stripe, font=bold, padding=(8, 3))
        style.configure("Section.TLabel", font=bold)
        style.configure("Prefix.TLabel", foreground=accent, background=stripe)
        style.configure("Warning.TLabel", foreground=warning)
        style.configure("Tooltip.TLabel", background=field, foreground=text, relief="solid")
        style.configure("TNotebook", background=page, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", padding=(20, 9), foreground=muted)
        style.map("TNotebook.Tab", background=[("selected", surface), ("active", stripe)],
                  foreground=[("selected", accent)])
        style.configure("TLabelframe", borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", font=bold, foreground=text)
        style.configure("TButton", padding=(12, 6), relief="flat")
        style.map("TButton", background=[("pressed", selected), ("active", stripe)])
        style.configure("Accent.TButton", background=accent, foreground=accent_text,
                        font=bold, lightcolor=accent, darkcolor=accent, bordercolor=accent)
        style.map("Accent.TButton", background=[("pressed", hover), ("active", hover)],
                  foreground=[("!disabled", accent_text)])
        style.configure("Danger.TButton", foreground=danger)
        style.map("Danger.TButton", foreground=[("!disabled", danger)])
        style.configure("Treeview", font=font, rowheight=row_height, borderwidth=1,
                        background=field, fieldbackground=field, foreground=text)
        style.configure("Treeview.Heading", font=bold, padding=(6, 6), relief="flat",
                        background=stripe, foreground=muted, lightcolor=stripe, darkcolor=stripe)
        style.map("Treeview", background=[("selected", selected)], foreground=[("selected", text)])
        style.configure("Horizontal.TProgressbar", background=accent, bordercolor=page,
                        lightcolor=accent, darkcolor=accent)
        self.configure(background=page)
        self.option_add("*Canvas.background", page)
        self.option_add("*Toplevel.background", surface)
        for canvas in getattr(self, "_page_canvases", ()):
            canvas.configure(background=page)
        for tree in (getattr(self, "s_tree", None), getattr(self, "g_tree", None)):
            if tree is not None:
                tree.tag_configure("stripe", background=stripe)

    def _change_theme(self):
        self._apply_theme()
        self.app_state.update_advanced_options({"theme": self.theme_var.get()})
        if hasattr(self, "trigger_save_cb"):
            self.trigger_save_cb()

    def build_ui(self):
        header = ttk.Frame(self, padding=(16, 4))
        header.pack(fill=tk.X)
        ttk.Label(header, text="KVTree", style="Brand.TLabel").pack(side=tk.LEFT)
        self.monitor_label = ttk.Label(header, style="Monitor.TLabel")
        self.monitor_label.pack(side=tk.RIGHT)
        self._update_monitor_label()

        footer = ttk.Frame(self, padding=(18, 8))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="就绪")
        status = ttk.Label(footer, textvariable=self.status_var, style="Muted.TLabel")
        status.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(footer, variable=self.progress_var, maximum=100, length=112)
        self.progress_bar.pack(side=tk.RIGHT, padx=(12, 0))
        footer.bind("<Configure>", lambda event: status.configure(wraplength=max(1, event.width - 156)))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 0))
        self.notebook.enable_traversal()
        self._page_canvases = []
        home, settings = (ScrollablePane(self.notebook) for _ in range(2))
        self.notebook.add(home, text="词库工作台")
        self.notebook.add(settings, text="偏好与高级设置")
        for pane in (home, settings):
            pane.body.configure(style="Page.TFrame", padding=(0, 12, 12, 8))
            self._page_canvases.append(pane.canvas)
        self.home_scrollable_frame, self.tab_settings = home.body, settings.body

        self._build_home_tab()
        self._build_settings_tab()
        self._apply_theme()

    def _update_monitor_label(self):
        text = "自动更新已开启" if self.auto_generate.get() else "自动更新已暂停"
        self.monitor_label.configure(text=text)

    def _build_home_tab(self):
        # Source Files
        s_frame = ttk.LabelFrame(self.home_scrollable_frame, text=" 笔记来源 ", padding=(12, 9))
        s_frame.pack(fill=tk.X, pady=(0, 9))
        
        tree_frame = ttk.Frame(s_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        self.s_tree = ttk.Treeview(tree_frame, columns=("path", "status", "action"), show="headings", height=3, selectmode="browse")
        self.s_tree.heading("path", text="文件或文件夹路径", anchor='w')
        self.s_tree.heading("status", text="状态", anchor='w')
        self.s_tree.heading("action", text="操作", anchor='c')
        self.s_tree.column("path", anchor='w')
        self.s_tree.column("status", width=76, minwidth=64, stretch=False, anchor="w")
        self.s_tree.column("action", width=66, minwidth=60, stretch=False, anchor="c")
        
        s_tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.s_tree.yview)
        self.s_tree.configure(yscrollcommand=s_tree_scroll.set)
        
        s_tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.s_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.s_tree.bind("<Button-1>", self.on_s_tree_click)
        self.s_tree.bind("<Return>", lambda event: self._open_source(self.s_tree.focus()))
        self.s_tree.bind("<Control-c>", lambda event: self._copy_tree_path(self.s_tree))
        
        sf_btns = ttk.Frame(s_frame)
        sf_btns.pack(fill=tk.X, side=tk.BOTTOM)
        btn_add_f = ttk.Button(sf_btns, text="添加文件", style="Accent.TButton", command=self.add_files)
        btn_add_f.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(btn_add_f, "支持多选，直接将指定的单个或多个 .md 笔记文件加入监控列表")
        
        btn_add_d = ttk.Button(sf_btns, text="添加文件夹", command=self.add_folder)
        btn_add_d.pack(side=tk.LEFT, padx=5)
        ToolTip(btn_add_d, "推荐！一键导入整个目录（如 Obsidian/Logseq 库目录），程序会自动扫描目录下的所有 .md 文件")
        
        btn_toggle = ttk.Button(sf_btns, text="启用 / 停用", command=self.toggle_s)
        btn_toggle.pack(side=tk.LEFT, padx=5)
        ToolTip(btn_toggle, "暂时停止或恢复对选中文件的监控与词库生成功能")
        
        btn_rm = ttk.Button(sf_btns, text="移除", command=self.remove_s)
        btn_rm.pack(side=tk.LEFT, padx=(5, 0))
        ToolTip(btn_rm, "将选中项从列表中彻底移除，停止为其生成独立词库")

        self.sources_summary = ttk.Label(sf_btns, style="Muted.TLabel")
        self.sources_summary.pack(side=tk.RIGHT)

        output = ttk.LabelFrame(self.home_scrollable_frame, text=" 导出目录 ", padding=(12, 9))
        output.pack(fill=tk.X, pady=(0, 9))
        path_row = ttk.Frame(output)
        path_row.pack(fill=tk.X)
        self.output_directory = tk.StringVar()
        ttk.Entry(path_row, textvariable=self.output_directory, state="readonly").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8)
        )
        ttk.Button(path_row, text="更改目录", command=self.select_o).pack(side=tk.LEFT)
        ttk.Button(path_row, text="打开目录", command=self.open_output_directory).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(path_row, text="删除已导出", style="Danger.TButton",
                   command=self.confirm_delete_exports).pack(side=tk.LEFT, padx=(6, 0))

        self.output_warning = ttk.Label(output, style="Warning.TLabel",
                                        text="导出目录位于笔记源内，生成的词库可能会被再次扫描。")
        self.output_warning.pack(anchor="w", pady=(6, 0))
        self.output_warning.pack_forget()

        controls = ttk.Frame(output)
        controls.pack(fill=tk.X, pady=(7, 0))
        ttk.Checkbutton(controls, text="后台自动更新", variable=self.auto_generate,
                        command=self.toggle_mon).pack(side=tk.LEFT)
        rescan = ttk.Button(controls, text="全量扫描并重建", command=self.confirm_and_queue_rescan)
        rescan.pack(side=tk.RIGHT)
        ToolTip(rescan, "重新读取启用的笔记，更新所有受影响的词库。")

        # Results View
        g_frame = ttk.LabelFrame(self.home_scrollable_frame, text=" 词库预览 ", padding=(12, 9))
        g_frame.pack(fill=tk.X)
        g_tree_subframe = ttk.Frame(g_frame)
        g_tree_subframe.pack(fill=tk.BOTH, expand=True)
        
        self.g_tree = ttk.Treeview(g_tree_subframe, columns=("output", "name", "source", "path", "action"), show="headings", height=6, selectmode="browse")
        self.g_tree.heading("output", text="导出", anchor='w')
        self.g_tree.heading("name", text="词库名称", anchor='w')
        self.g_tree.heading("source", text="词库数据来源", anchor='w')
        self.g_tree.heading("path", text="文件路径", anchor='w')
        self.g_tree.heading("action", text="操作", anchor='c')
        self.g_tree.column("output", width=62, minwidth=58, stretch=False, anchor="c")
        self.g_tree.column("name", width=182, minwidth=120)
        self.g_tree.column("source", width=145, minwidth=92)
        self.g_tree.column("path", width=290, minwidth=130)
        self.g_tree.column("action", width=66, minwidth=60, stretch=False, anchor="c")
        
        g_tree_scroll = ttk.Scrollbar(g_tree_subframe, orient="vertical", command=self.g_tree.yview)
        self.g_tree.configure(yscrollcommand=g_tree_scroll.set)
        g_tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.g_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.g_tree.bind("<Button-1>", self.on_g_tree_click)
        self.g_tree.bind("<space>", self._toggle_focused_export)
        self.g_tree.bind("<Return>", lambda event: self._open_export(self.g_tree.focus()))
        self.g_tree.bind("<Control-c>", lambda event: self._copy_tree_path(self.g_tree))
        self._source_tip = None
        self._source_tip_after = None
        self._source_tip_row = None
        self.g_tree.bind('<Motion>', self._hover_output_source)
        self.g_tree.bind('<Leave>', self._hide_source_tip)
        
        g_btn_frame = ttk.Frame(g_frame)
        g_btn_frame.pack(fill=tk.X, pady=(8, 0))
        self.outputs_summary = ttk.Label(g_btn_frame, style="Muted.TLabel")
        self.outputs_summary.pack(side=tk.LEFT)
        btn_blacklist = ttk.Button(g_btn_frame, text="词库黑名单", command=self.manage_blacklist)
        btn_blacklist.pack(side=tk.RIGHT)
        ToolTip(btn_blacklist, "配置哪些词库标签要被永久剔除，不显示也不生成文件")
        
    def _build_settings_tab(self):
        opts = self.app_state.get_advanced_options()
        left = ttk.Frame(self.tab_settings, style="Page.TFrame")
        right = ttk.Frame(self.tab_settings, style="Page.TFrame")
        self.tab_settings.columnconfigure((0, 1), weight=1, uniform="settings")
        left.grid(row=0, column=0, sticky="new", padx=(0, 6))
        right.grid(row=0, column=1, sticky="new", padx=(6, 0))

        rules = ttk.LabelFrame(left, text=" 数据清洗 ", padding=12)
        rules.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(rules, text="按规则过滤整行，或替换行内匹配的文本。",
                  style="Muted.TLabel").pack(anchor="w")
        ttk.Button(rules, text="管理过滤规则", style="Accent.TButton",
                   command=self.manage_rules).pack(anchor="w", pady=(8, 0))

        logseq = ttk.LabelFrame(left, text=" Logseq 属性扫描 ", padding=12)
        logseq.pack(fill=tk.X)
        self.scan_keys_var = tk.BooleanVar(value=opts.get("logseq_scan_keys", False))
        self.scan_values_var = tk.BooleanVar(value=opts.get("logseq_scan_values", False))
        self.scan_pure_values_var = tk.BooleanVar(value=opts.get("logseq_scan_pure_values", False))
        for label, variable in (
            ("提取属性键", self.scan_keys_var),
            ("提取双方括号属性值", self.scan_values_var),
            ("提取纯文本属性值", self.scan_pure_values_var),
        ):
            ttk.Checkbutton(logseq, text=label, variable=variable,
                            command=self.save_settings_from_tab).pack(anchor="w", pady=3)
        ttk.Button(logseq, text="管理 Logseq 排除属性键",
                   command=self.manage_logseq_excludes).pack(anchor="w", pady=(8, 0))

        common = ttk.LabelFrame(right, text=" 常规偏好 ", padding=12)
        common.pack(fill=tk.X, pady=(0, 10))
        appearance = ttk.Frame(common)
        appearance.pack(anchor="w", pady=(0, 5))
        ttk.Label(appearance, text="外观主题").pack(side=tk.LEFT, padx=(0, 10))
        for label, value in (("亮色", "light"), ("暗色", "dark")):
            ttk.Radiobutton(appearance, text=label, value=value, variable=self.theme_var,
                            command=self._change_theme).pack(side=tk.LEFT, padx=(0, 8))

        self.run_on_startup_var = tk.BooleanVar(value=opts.get("run_on_startup", False))
        self.minimize_to_tray_var = tk.BooleanVar(value=opts.get("minimize_to_tray", True))
        for label, variable in (
            ("随 Windows 启动", self.run_on_startup_var),
            ("关闭窗口时隐藏到托盘", self.minimize_to_tray_var),
        ):
            ttk.Checkbutton(common, text=label, variable=variable,
                            command=self.save_settings_from_tab).pack(anchor="w", pady=3)
        ttk.Button(common, text="排除文件夹 / 文件路径",
                   command=self.manage_excluded_paths).pack(anchor="w", pady=(8, 0))

        maintenance = ttk.LabelFrame(right, text=" 数据维护 ", padding=12)
        maintenance.pack(fill=tk.X, pady=(0, 10))
        self.clear_config_var = tk.BooleanVar(value=False)
        self.clear_cache_var = tk.BooleanVar(value=False)
        self.clear_output_var = tk.BooleanVar(value=False)
        for label, variable in (
            ("重置设置和规则（同时清除解析缓存）", self.clear_config_var),
            ("清除解析缓存", self.clear_cache_var),
            ("删除已导出的词库文件", self.clear_output_var),
        ):
            ttk.Checkbutton(maintenance, text=label, variable=variable).pack(anchor="w", pady=3)
        ttk.Button(maintenance, text="清除所选数据", style="Danger.TButton",
                   command=self.clear_personal_data).pack(anchor="w", pady=(8, 0))

        ttk.Button(right, text="帮助与更新 · GitHub",
                   command=lambda: webbrowser.open("https://github.com/EllisMorrow/KV-Tree")
                   ).pack(anchor="w")

    def _on_ui_thread(self, callback):
        """Wrap callbacks invoked by worker threads for Tk-safe execution."""
        def schedule_callback(*args, **kwargs):
            self.call_on_ui_thread(callback, *args, **kwargs)
        return schedule_callback

    def call_on_ui_thread(self, callback, *args, **kwargs):
        """Queue work without calling any Tk API from a worker thread."""
        self._ui_callback_queue.put((callback, args, kwargs))

    def _drain_ui_callbacks(self):
        """Execute queued callbacks exclusively from Tk's main event loop."""
        while True:
            try:
                callback, args, kwargs = self._ui_callback_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args, **kwargs)
            except Exception:
                self.report_callback_exception(*sys.exc_info())
        try:
            self.after(25, self._drain_ui_callbacks)
        except tk.TclError:
            # A queued close callback may have destroyed the root window.
            pass

    def save_settings_from_tab(self):
        # Rules and logseq_exclude_keys are now saved instantly when their respective dialogs close.
        # Only check if common options or logseq booleans changed.
        opts = self.app_state.get_advanced_options()
        startup_changed = opts.get("run_on_startup") != self.run_on_startup_var.get()
        
        new_opts = {
            "logseq_scan_keys": self.scan_keys_var.get(),
            "logseq_scan_values": self.scan_values_var.get(),
            "logseq_scan_pure_values": self.scan_pure_values_var.get(),
            "run_on_startup": self.run_on_startup_var.get(),
            "minimize_to_tray": self.minimize_to_tray_var.get(),
            "auto_generate": self.auto_generate.get()
        }
        self.app_state.update_advanced_options(new_opts)
        
        if startup_changed:
            self.set_startup(new_opts.get("run_on_startup"))
        
        # 检查是否有解析相关选项变化，使用防抖机制避免连续三次点击触发三次重建
        parse_opts_changed = (
            opts.get("logseq_scan_keys", False) != new_opts.get("logseq_scan_keys", False) or
            opts.get("logseq_scan_values", False) != new_opts.get("logseq_scan_values", False) or
            opts.get("logseq_scan_pure_values", False) != new_opts.get("logseq_scan_pure_values", False)
        )
        
        if parse_opts_changed:
            # 防抖：取消上一个待执行的重建计时器，重新计时 2 秒
            if hasattr(self, '_rebuild_timer_id') and self._rebuild_timer_id:
                self.after_cancel(self._rebuild_timer_id)
            self.set_status("扫描选项已变更，2秒后自动重建词库...")
            self._rebuild_timer_id = self.after(2000, self._debounced_rebuild)
        else:
            self.set_status("设置已保存。")
        
        # 即时持久化
        if hasattr(self, 'trigger_save_cb'): self.trigger_save_cb()
    
    def _debounced_rebuild(self):
        """防抖回调：静默触发重建，使用 full_rescan 而非 clear_cache 避免删除缓存文件"""
        self._rebuild_timer_id = None
        self.set_status("正在后台重建词库（选项变更）...")
        self.dispatcher.put_task(("full_rescan",))

    def clear_personal_data(self):
        if not any(var.get() for var in (self.clear_config_var, self.clear_cache_var, self.clear_output_var)):
            messagebox.showinfo("清除数据", "请选择要清除的数据。", parent=self)
            return
        msg = "这将清除您勾选的个人数据，重置软件。\n\n• 您的源 .md 笔记文件【绝不】受影响。\n• 清除后软件将立即退出，需要您手动重新打开。\n\n确认清除吗？"
        if messagebox.askyesno("⚠️ 危险操作确认", msg, icon='warning'):
            self.app_state.skip_save = True
            self.tray_icon.stop()
            self.file_monitor.stop()
            self.dispatcher.stop()
            output_root = self.app_state.get_output_path()
            data_dir = os.path.abspath("用户数据")
            config_path = os.path.join(data_dir, "kv_tree_config.json")
            cache_path = os.path.join(data_dir, "parsing_cache.json")
            
            try:
                if self.clear_config_var.get() and os.path.exists(config_path):
                    os.remove(config_path)
                    if os.path.exists(cache_path):
                        os.remove(cache_path) # Force wipe cache to clear old absolute paths
                    self.app_state.set_output_path("")
                    self.app_state.set_rules({"line_rules": [], "content_rules": []})
                    self.update_output_directory()
                    
                if self.clear_cache_var.get() and os.path.exists(cache_path):
                    os.remove(cache_path)
                if self.clear_output_var.get():
                    _, errors = self.dispatcher.delete_active_exports(output_root)
                    if errors:
                        raise OSError("\n".join(errors))
            except Exception as e:
                messagebox.showerror("清除失败", f"清除失败: {e}\n\n请手动删除对应文件。")
            else:
                messagebox.showinfo("清除成功", "所选数据已被清除！\n程序即将退出，请手动重新运行。")
            
            self.destroy()

    def load_state_to_ui(self):
        self.update_output_directory()
        self.update_lists()

    def update_output_directory(self):
        path = self.app_state.get_output_path()
        if not path or path == os.getcwd():
            self.output_directory.set("未设置导出目录")
            self.output_warning.pack_forget()
            return

        self.output_directory.set(path)
        resolved = normalize(path)
        overlaps = any(
            kind == "folder" and _is_within_folder(resolved, normalize(source))
            for source, kind, _ in self.app_state.get_source_roots()
        )
        if overlaps:
            self.output_warning.pack(anchor="w", pady=(6, 0))
        else:
            self.output_warning.pack_forget()

    def update_lists(self):
        self.update_source_list()
        self.update_generated_list()
        
    def update_progress(self, val=None, mode=None):
        if mode:
            self.progress_bar.config(mode=mode)
        if val is not None:
            self.progress_var.set(val)
            
    def set_status(self, msg):
        self.status_var.set(msg)
        self.update_idletasks()
        
    def show_error(self, title, msg):
        messagebox.showerror(title, msg)

    def open_output_directory(self):
        path = self.app_state.get_output_path()
        if path and path != os.getcwd() and os.path.isdir(path):
            self._open_path(path)
        else:
            self.show_error("打开目录", "请先设置有效的导出目录。")

    def confirm_delete_exports(self):
        path = self.app_state.get_output_path()
        if not path or path == os.getcwd():
            self.show_error("删除导出", "请先设置导出目录。")
            return
        if messagebox.askyesno("确认删除", f"删除以下目录中的 KVTree 导出文件？\n\n{path}", parent=self):
            self.dispatcher.put_task(("delete_exports", path))

    def _show_export_deletion_result(self, count, errors):
        if errors:
            self.show_error("部分词库未删除", f"已删除 {count} 个词库。\n" + "\n".join(errors))
        else:
            messagebox.showinfo("删除完毕", f"已成功删除 {count} 个词库文件。")

    def on_g_tree_click(self, event):
        if self.g_tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.g_tree.identify_row(event.y)
        column = self.g_tree.identify_column(event.x)
        if not row:
            return
        if column == "#1":
            self._toggle_export(row)
        elif column == "#3":
            self._hover_output_source(event)
        elif column == "#5":
            self._open_export(row)

    def _toggle_focused_export(self, event):
        self._toggle_export(self.g_tree.focus())
        return "break"

    def _toggle_export(self, path):
        if not path or not self.g_tree.exists(path):
            return
        name = os.path.basename(path)
        selected = not self.app_state.get_output_selection().get(name, False)
        self.app_state.set_output_selection(name, selected)
        self.dispatcher.put_task(("regenerate_output", path))
        self.g_tree.set(path, "output", "☑" if selected else "☐")
        self._update_outputs_summary()
        if hasattr(self, "trigger_save_cb"):
            self.trigger_save_cb()

    def _open_path(self, path):
        try:
            os.startfile(path)
        except OSError as error:
            self.show_error("无法打开", str(error))

    def _open_export(self, path):
        if not path:
            return
        if os.path.isfile(path):
            self._open_path(path)
        else:
            self.show_error("打开词库", "该词库文件尚未生成或已被删除。")

    def _copy_tree_path(self, tree):
        path = tree.focus()
        if path:
            self.clipboard_clear()
            self.clipboard_append(path)
            self.set_status(f"已复制路径：{path}")
        return "break"

    def on_closing(self, from_tray=False):
        opts = self.app_state.get_advanced_options()
        if not from_tray and opts.get("minimize_to_tray", True): 
            self.withdraw()
            return
            
        # Save geometry before exit
        self.app_state.set_window_geometry(self.geometry())
            
        self.tray_icon.stop()
        self.file_monitor.stop()
        self.dispatcher.stop()
        self.destroy()

    def manage_blacklist(self):
        all_possible_basenames = set()
        
        # 1. Start with active session outputs
        for path in self.app_state.get_active_outputs().keys():
            all_possible_basenames.add(os.path.basename(path))
            
        # 2. Add existing '.md' files physically in the output directory
        out_dir = self.app_state.get_output_path()
        if os.path.exists(out_dir):
            try:
                for file in os.listdir(out_dir):
                    if file.endswith(".md"):
                        all_possible_basenames.add(file)
            except OSError: pass
                
        all_possible_basenames.update(self.dispatcher.cache_manager.get_output_names())

        dialog = BlacklistWindow(self, all_possible_basenames, self.app_state.get_blacklist)
        saved, new_blacklist = dialog.show()
        if saved:
            current = self.app_state.get_blacklist()
            for b in current:
                self.app_state.remove_from_blacklist(b)
            for b in new_blacklist:
                self.app_state.add_to_blacklist(b)
            
            changed_names = current.symmetric_difference(new_blacklist)
            if changed_names:
                self.dispatcher.put_task(("initialize", changed_names))
                if hasattr(self, 'trigger_save_cb'):
                    self.trigger_save_cb()

    def manage_rules(self):
        from src.ui.components import DualRuleWindow
        current_rules = self.app_state.get_rules()
        
        dialog = DualRuleWindow(self, rules_dict=current_rules)
        saved_items = dialog.show()
        if saved_items is not None:
            if saved_items != current_rules:
                self.app_state.set_rules(saved_items)
                if messagebox.askyesno("更新", "过滤规则已修改，是否立即重建词库？"):
                    self.dispatcher.put_task(("clear_cache",))

    def manage_logseq_excludes(self):
        current_keys = self.app_state.get_logseq_exclude_keys()
        instruction = "填入你要排除的 Logseq 属性键。\n• 精确匹配：如填入 alias，所有 alias:: 行会被跳过。\n• 前缀匹配：如填入 card-*，所有以 card- 开头的键都会被跳过（card-repeats, card-ease-factor 等）。"
        
        dialog = DynamicListWindow(
            self, 
            title="🚫 Logseq 排除属性键", 
            instruction=instruction, 
            initial_items=current_keys,
            placeholder="属性键名称，如：alias"
        )
        saved_items = dialog.show()
        if saved_items is not None:
            if saved_items != current_keys:
                self.app_state.set_logseq_exclude_keys(saved_items)
                if messagebox.askyesno("更新", "Logseq 排除属性已修改，是否立即全量重建词库？"):
                    self.dispatcher.put_task(("clear_cache",))

    def manage_excluded_paths(self):
        dialog = tk.Toplevel(self)
        dialog.title('排除文件夹 / 文件路径')
        dialog.geometry('660x420')
        dialog.minsize(480, 280)
        dialog.transient(self)
        ttk.Label(dialog, text='每行一条。同名文件夹会在所有源中跳过，其子内容也会跳过。\n也可填写文件名、相对路径或完整文件 / 文件夹路径。保存后立即重新核对词库。',
                  style='Muted.TLabel', padding=12, wraplength=440).pack(fill='x')
        dark = self.theme_var.get() == 'dark'
        editor_frame = ttk.Frame(dialog)
        editor = tk.Text(editor_frame, width=1, height=1, wrap='none', padx=10, pady=8,
                         font=('Consolas', 11), undo=True, relief='flat',
                         background='#343942' if dark else 'white',
                         foreground='#F0F2F5' if dark else '#201F1E',
                         insertbackground='white' if dark else 'black')
        scroll = ttk.Scrollbar(editor_frame, orient='vertical', command=editor.yview)
        editor.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        editor.pack(side='left', fill='both', expand=True)
        editor.insert('1.0', '\n'.join(self.app_state.get_excluded_paths()))
        def save():
            self.app_state.set_excluded_paths(editor.get('1.0', 'end').splitlines())
            if hasattr(self, 'trigger_save_cb'):
                self.trigger_save_cb()
            self.dispatcher.put_task(('initialize',))
            dialog.destroy()
        buttons = ttk.Frame(dialog, padding=12)
        buttons.pack(side='bottom', fill='x')
        ttk.Button(buttons, text='保存', style="Accent.TButton", command=save).pack(side='right')
        ttk.Button(buttons, text='取消', command=dialog.destroy).pack(side='right', padx=8)
        editor_frame.pack(fill='both', expand=True, padx=12)
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        dialog.bind('<Control-Return>', lambda event: save())
        editor.focus_set()
        dialog.grab_set()
        self.wait_window(dialog)

    def _hide_source_tip(self, event=None):
        if self._source_tip_after:
            self.after_cancel(self._source_tip_after)
            self._source_tip_after = None
        if self._source_tip:
            self._source_tip.destroy()
            self._source_tip = None
        self._source_tip_row = None

    def _hover_output_source(self, event):
        row = self.g_tree.identify_row(event.y)
        if self.g_tree.identify_column(event.x) != '#3' or not row:
            self._hide_source_tip()
            return
        if row == self._source_tip_row:
            return
        self._hide_source_tip()
        self._source_tip_row = row
        x, y = event.x_root + 14, event.y_root + 18
        def show():
            self._source_tip_after = None
            if not self.g_tree.exists(row):
                return
            roots = self._output_sources.get(os.path.basename(row), ())
            if not roots:
                return
            tip = self._source_tip = tk.Toplevel(self)
            tip.overrideredirect(True)
            tip.attributes('-topmost', True)
            ttk.Label(tip, text='词库数据来源\n' + '\n'.join(sorted(roots)),
                      style="Tooltip.TLabel", padding=10, justify='left', wraplength=680).pack()
            tip.update_idletasks()
            left = min(x, max(0, self.winfo_screenwidth() - tip.winfo_reqwidth()))
            top = y if y + tip.winfo_reqheight() <= self.winfo_screenheight() else y - tip.winfo_reqheight() - 24
            tip.geometry(f'{left:+d}{max(0, top):+d}')
        self._source_tip_after = self.after(450, show)

    def _prepare_list_refresh(self, dirty_outputs=None):
        cache = self.dispatcher.cache_manager
        full_refresh = dirty_outputs is None
        names = cache.get_output_names() if full_refresh else {
            os.path.basename(path) for path in dirty_outputs
        }
        roots = [(path, normalize(path), kind)
                 for path, kind in self.app_state.get_enabled_source_roots()]
        changes = {
            name: self.app_state.get_source_owners(cache.get_output_sources(name), roots)
            for name in names
        }
        self._on_ui_thread(self._apply_list_refresh)(changes, full_refresh)

    def _apply_list_refresh(self, changes, full_refresh):
        if not full_refresh and all(
            roots == self._output_sources.get(name, set()) for name, roots in changes.items()
        ):
            return
        if full_refresh:
            self._output_sources.clear()
        for name, roots in changes.items():
            if roots:
                self._output_sources[name] = roots
            else:
                self._output_sources.pop(name, None)
        self.update_lists()

    def update_generated_list(self):
        if hasattr(self, '_source_tip'):
            self._hide_source_tip()
        rows = {}
        active_outputs = self.app_state.get_active_outputs()
        output_selection = self.app_state.get_output_selection()
        blacklist = self.app_state.get_blacklist()
        
        for f_path, source_path in sorted(active_outputs.items()):
            basename = os.path.basename(f_path)
            if basename in blacklist:
                continue
                
            if basename not in output_selection: 
                self.app_state.set_output_selection(basename, False)
            
            is_checked = output_selection.get(basename, False)
            check_char = "☑" if is_checked else "☐"
            display_source = source_path if len(source_path) < 50 else "..." + source_path[-47:]
            roots = self._output_sources.get(basename, ())
            if roots:
                display_source = os.path.basename(next(iter(roots))) if len(roots) == 1 else f"{len(roots)} 个导入来源"
            elif display_source == "多元":
                display_source = "词库来源"
            rows[f_path] = (check_char, basename, display_source, f_path, "打开")
        self._sync_tree_rows(self.g_tree, rows)
        self.g_tree.configure(height=max(2, min(6, len(rows))))
        self._update_outputs_summary()

    def _update_outputs_summary(self):
        items = self.g_tree.get_children()
        if not items:
            self.outputs_summary.configure(text="暂无词库 · 在笔记行末添加 #KV树-名称")
            return
        selected = self.app_state.get_output_selection()
        checked = sum(bool(selected.get(os.path.basename(item))) for item in items)
        self.outputs_summary.configure(text=f"{len(items)} 个词库 · 已勾选 {checked} 个导出")

    @staticmethod
    def _sync_tree_rows(tree, rows):
        # Updating rows in place retains selection, focus and scroll position.
        previous = tree.get_children()
        existing = set(previous)
        removed = existing - rows.keys()
        if removed:
            tree.delete(*removed)
        retained = existing & rows.keys()
        reorder = ([item for item in previous if item in retained]
                   != [item for item in rows if item in retained])
        for index, (item, values) in enumerate(rows.items()):
            tags = ("stripe",) if index % 2 else ()
            if item not in retained:
                tree.insert("", index, iid=item, values=values, tags=tags)
            else:
                if reorder:
                    tree.move(item, "", index)
                if tree.item(item, "values") != values or tree.item(item, "tags") != tags:
                    tree.item(item, values=values, tags=tags)

    def _restart_monitor_if_auto(self):
        if self.auto_generate.get():
            self.file_monitor.start()

    def update_source_list(self):
        self.update_output_directory()
        roots = self.app_state.get_source_roots()
        rows = {
            path: (f"[{'文件夹' if kind == 'folder' else '文件'}] {path}", "已启用" if enabled else "已停用", "打开")
            for path, kind, enabled in roots
        }
        self._sync_tree_rows(self.s_tree, rows)
        self.s_tree.configure(height=max(2, min(3, len(rows))))
        enabled = sum(bool(active) for _, _, active in roots)
        self.sources_summary.configure(text=f"{enabled} / {len(roots)} 个来源已启用" if roots else "尚未添加笔记来源")

    def add_files(self):
        files = filedialog.askopenfilenames(title="选择.md文件", filetypes=(("Markdown", "*.md"), ("All files", "*.*")))
        if not files: return
        sources = self.app_state.get_source_files()
        for f in files:
            f_path = os.path.normpath(f)
            if f_path not in sources: 
                self.app_state.update_source_file(f_path, {"enabled": True, "mtime": 0, "type": "file"})
        self.update_source_list()
        self.set_status(f"已添加 {len(files)} 个文件。")
        self.dispatcher.put_task(("initialize",))
        self._restart_monitor_if_auto()
        if hasattr(self, 'trigger_save_cb'): self.trigger_save_cb()

    def add_folder(self):
        folder_path = filedialog.askdirectory(title="选择文件夹")
        if not folder_path:
            return
        folder_path = os.path.normpath(folder_path)
        if folder_path in self.app_state.get_source_files(): 
            messagebox.showinfo("提示", "该文件夹已在源列表中。")
            return
        self.dispatcher.put_task(("scan_folder", folder_path))

    def toggle_s(self):
        s = self.s_tree.focus()
        if s: 
            sources = self.app_state.get_source_files()
            data = sources[s]
            data["enabled"] = not data.get("enabled", False)
            self.app_state.update_source_file(s, data)
            self.update_source_list()
            self.dispatcher.put_task(("initialize",))
            self._restart_monitor_if_auto()
            if hasattr(self, 'trigger_save_cb'): self.trigger_save_cb()

    def on_s_tree_click(self, event):
        if self.s_tree.identify_region(event.x, event.y) != "cell": return
        column_id = self.s_tree.identify_column(event.x)
        if column_id != "#3": return
        
        item_id = self.s_tree.identify_row(event.y)
        if not item_id: return
        
        self._open_source(item_id)

    def _open_source(self, path):
        if not path:
            return
        target = os.path.dirname(path) if os.path.isfile(path) else path
        if os.path.isdir(target):
            self._open_path(target)
        else:
            self.show_error("打开来源", "该来源路径已经不存在。")

    def remove_s(self):
        selected_id = self.s_tree.focus()
        if not selected_id: return
        sources = self.app_state.get_source_files()
        is_folder = sources.get(selected_id, {}).get("type") == "folder"
        
        if messagebox.askyesno("确认移除", f"确定要移除 '{selected_id}' 吗？"):
            self.app_state.remove_source_file(selected_id)
            self.update_source_list()
            if is_folder:
                choice = messagebox.askquestion("清理选项", "您移除了文件夹，清理全量缓存吗？\n(是：推荐 / 否：缓释)", type=messagebox.YESNOCANCEL)
                if choice == 'yes': self.dispatcher.put_task(("clear_cache",))
                else: self.dispatcher.put_task(("initialize",))
            else:
                # The source still exists on disk, so a synthetic filesystem
                # deletion would be treated as a modification. Reconcile the
                # source set instead so its cached contributions are removed.
                self.dispatcher.put_task(("initialize",))
            self.set_status(f"'{os.path.basename(selected_id)}' 已移除。")
            self._restart_monitor_if_auto()
            if hasattr(self, 'trigger_save_cb'): self.trigger_save_cb()

    def _show_scan_results_and_add(self, folder_path, scanned_files):
        file_count = len(scanned_files)
        if file_count == 0:
            messagebox.showinfo("扫完", f"'{os.path.basename(folder_path)}' 中无.md。")
            return
        if messagebox.askyesno("确认", f"发现 {file_count} 个.md文件，确认添加到源列表？"):
            self.app_state.update_source_file(folder_path, {"enabled": True, "type": "folder", "files": scanned_files, "mtime": os.path.getmtime(folder_path)})
            self.update_source_list()
            self.dispatcher.put_task(("initialize",))
            self.set_status(f"已添加文件夹: {folder_path}...")
            self._restart_monitor_if_auto()
            if hasattr(self, 'trigger_save_cb'): self.trigger_save_cb()

    def select_o(self):
        p = filedialog.askdirectory()
        if p: 
            self.app_state.set_output_path(p)
            self.update_output_directory()
            self.dispatcher.put_task(("full_rescan",))

    def toggle_mon(self):
        is_auto = self.auto_generate.get()
        opts = self.app_state.get_advanced_options()
        opts["auto_generate"] = is_auto
        self.app_state.update_advanced_options(opts)
        
        self._update_monitor_label()
        if is_auto:
            self.file_monitor.start()
        else:
            self.file_monitor.stop()
        if hasattr(self, "trigger_save_cb"):
            self.trigger_save_cb()

    def confirm_and_queue_rescan(self):
        if messagebox.askyesno("重建确认", "将全量重构词库，期间后台运行，确认执行吗？"):
            self.dispatcher.put_task(("full_rescan",))

    def set_startup(self, enable):
        if not winreg: return
        app_name = "KVTreeApp"
        app_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"' if 'python' in sys.executable.lower() else f'"{os.path.abspath(sys.executable)}"'
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                if enable: winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, app_path)
                else:
                    try: winreg.DeleteValue(key, app_name)
                    except FileNotFoundError: pass
        except Exception: pass

    def on_minimize(self, event):
        opts = self.app_state.get_advanced_options()
        if self.state() == 'iconic' and opts.get("minimize_to_tray", True): 
            self.after(10, self.withdraw)

    def hide_to_tray(self):
        opts = self.app_state.get_advanced_options()
        if opts.get("minimize_to_tray", True): self.withdraw()
        else: self.on_closing()
