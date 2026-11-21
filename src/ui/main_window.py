import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import queue
import sys
import webbrowser
import ctypes

from src.ui.tray_icon import AppTrayIcon
from src.logic.path_filter import normalize
from src.logic.file_monitor import _is_within_folder
from src.ui.components import ToolTip, AdvancedOptionsWindow, BlacklistWindow, DynamicListWindow

try:
    import winreg
except ImportError:
    winreg = None

class KvTreeAppUI(tk.Tk):
    VERSION = "1.3.0"

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
            'show_error': self._on_ui_thread(self.show_error)
        }
        self.file_monitor.ui_cb = self.dispatcher.ui_cb

        self.title(f"KVTree - v{self.VERSION} (Official)")
        self.state("normal")
        
        # Load previous geometry if exists, otherwise center to default 1050x700
        saved_geo = self.app_state.get_window_geometry()
        if saved_geo:
            self.geometry(saved_geo)
        else:
            self.update_idletasks()
            x = (self.winfo_screenwidth() - 1050) // 2
            y = (self.winfo_screenheight() - 700) // 2
            self.geometry(f"1050x700+{x}+{y}")
            
        self.minsize(800, 600)  # Prevent user from making it too small
        
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

    def _apply_theme(self):
        dark = self.theme_var.get() == "dark"
        style = ttk.Style(self)
        try:
            style.theme_use('clam' if dark else 'vista')
        except tk.TclError:
            style.theme_use('clam')
        
        # Modern UI Polish
        default_font = ("Microsoft YaHei UI", 10)
        bold_font = ("Microsoft YaHei UI", 10, "bold")
        
        style.configure(".", font=default_font)
        style.configure("TLabel", font=default_font)
        style.configure("TButton", font=default_font, padding=6)
        style.configure("TCheckbutton", font=default_font)
        style.configure("TLabelframe.Label", font=bold_font, foreground="#75BEFF" if dark else "#0078D4")
        style.configure("Muted.TLabel", foreground="#B8BEC7" if dark else "#666666")
        style.configure("Link.TLabel", foreground="#75BEFF" if dark else "#0078D4")
        
        style.configure("TNotebook.Tab", font=default_font, padding=[15, 6])
        
        style.configure("Treeview", font=default_font, rowheight=30, borderwidth=0)
        style.configure("Treeview.Heading", font=bold_font, background="#F3F2F1", foreground="#201F1E", borderwidth=1, lightcolor="#F3F2F1", darkcolor="#F3F2F1")
        style.map("Treeview", background=[('selected', '#CBE8F6')], foreground=[('selected', '#000000')])
        
        if dark:
            bg, surface, fg = "#25282D", "#343942", "#F0F2F5"
            style.configure(".", background=bg, foreground=fg, fieldbackground=surface,
                            bordercolor="#626A76", lightcolor=surface, darkcolor=surface,
                            troughcolor=bg, arrowcolor=fg, selectbackground="#285880",
                            selectforeground=fg, insertcolor=fg)
            style.configure("Treeview", background=surface, fieldbackground=surface, foreground=fg)
            style.configure("Treeview.Heading", background=surface, foreground=fg,
                            lightcolor=surface, darkcolor=surface)
            style.map("Treeview", background=[("selected", "#285880")], foreground=[("selected", fg)])
            for widget_style in ("TButton", "TCheckbutton", "TNotebook.Tab", "Treeview.Heading", "Vertical.TScrollbar"):
                style.map(widget_style, background=[("selected", surface), ("active", "#45505D")],
                          foreground=[("disabled", "#9299A4"), ("!disabled", fg)])
        else:
            bg = "#F0F0F0"
            style.configure(".", background=bg, foreground="#201F1E", fieldbackground="white",
                            bordercolor="#AAAAAA", lightcolor="#F3F2F1", darkcolor="#D8D8D8",
                            troughcolor="#EEEEEE", arrowcolor="#201F1E", insertcolor="#201F1E")
            style.configure("Treeview", background="white", fieldbackground="white", foreground="#201F1E")
            for widget_style in ("TButton", "TCheckbutton", "TNotebook.Tab", "Treeview.Heading", "Vertical.TScrollbar"):
                style.map(widget_style, background=[("selected", "#FFFFFF"), ("active", "#E2EBF4")],
                          foreground=[("disabled", "#777777"), ("!disabled", "#201F1E")])
        self.configure(background=bg)
        self.option_add("*Canvas.background", bg)
        self.option_add("*Toplevel.background", bg)
        if hasattr(self, "home_canvas"):
            self.home_canvas.configure(background=bg)

    def _change_theme(self):
        self._apply_theme()
        self.app_state.update_advanced_options({"theme": self.theme_var.get()})
        if hasattr(self, "trigger_save_cb"):
            self.trigger_save_cb()

    def build_ui(self):
        # PACK BOTTOM BAR FIRST SO IT NEVER COLLAPSES!
        bottom_frame = ttk.Frame(self)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bottom_frame, textvariable=self.status_var, relief=tk.SUNKEN).pack(side=tk.BOTTOM, fill=tk.X)
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(bottom_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(5, 5))

        # Main Notebook structure for Tabs
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 5))
        
        self.tab_home = ttk.Frame(self.notebook, padding="10")
        self.tab_settings = ttk.Frame(self.notebook, padding="10")
        
        self.notebook.add(self.tab_home, text=" 🏠 词库转换控制台 ")
        self.notebook.add(self.tab_settings, text=" 🛠️ 偏好与高级设置 ")
        
        self._build_home_tab()
        self._build_settings_tab()

    def _build_home_tab(self):
        # 1. Master Scrollable Canvas for Home Tab
        canvas = tk.Canvas(self.tab_home, borderwidth=0, highlightthickness=0)
        self.home_canvas = canvas
        v_scrollbar = ttk.Scrollbar(self.tab_home, orient="vertical", command=canvas.yview)
        
        self.home_scrollable_frame = ttk.Frame(canvas)
        
        self.home_scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        # Configure canvas to resize inner frame width to match canvas width
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfig(window_id, width=e.width)
        )
        
        window_id = canvas.create_window((0, 0), window=self.home_scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=v_scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        v_scrollbar.pack(side="right", fill="y")
        
        # Add Mousewheel support for the entire Home Tab canvas
        def _on_mousewheel(event):
            # Prevent scrolling canvas if hovering over a Treeview (let Treeview scroll itself)
            if "treeview" in str(event.widget).lower():
                return
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            
        self.tab_home.bind("<Enter>", lambda e: self.tab_home.bind_all("<MouseWheel>", _on_mousewheel))
        self.tab_home.bind("<Leave>", lambda e: self.tab_home.unbind_all("<MouseWheel>"))
        
        # Source Files
        s_frame = ttk.LabelFrame(self.home_scrollable_frame, text=" 第一步：导入需要构建词库的 Markdown 笔记源 ", padding="15")
        s_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        tree_frame = ttk.Frame(s_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        self.s_tree = ttk.Treeview(tree_frame, columns=("path", "status", "action"), show="headings", height=5)
        self.s_tree.heading("path", text="目标路径 (支持单独文件或整个库文件夹)", anchor='w')
        self.s_tree.heading("status", text="监控状态", anchor='w')
        self.s_tree.heading("action", text="操作", anchor='c')
        self.s_tree.column("path", anchor='w')
        self.s_tree.column("status", width=80, anchor='w')
        self.s_tree.column("action", width=80, anchor='c')
        
        s_tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.s_tree.yview)
        self.s_tree.configure(yscrollcommand=s_tree_scroll.set)
        
        s_tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.s_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.s_tree.bind("<Button-1>", self.on_s_tree_click)
        
        sf_btns = ttk.Frame(s_frame)
        sf_btns.pack(fill=tk.X, side=tk.BOTTOM)
        btn_add_f = ttk.Button(sf_btns, text="添加文件", command=self.add_files)
        btn_add_f.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(btn_add_f, "支持多选，直接将指定的单个或多个 .md 笔记文件加入监控列表")
        
        btn_add_d = ttk.Button(sf_btns, text="添加文件夹", command=self.add_folder)
        btn_add_d.pack(side=tk.LEFT, padx=5)
        ToolTip(btn_add_d, "推荐！一键导入整个目录（如 Obsidian/Logseq 库目录），程序会自动扫描目录下的所有 .md 文件")
        
        btn_toggle = ttk.Button(sf_btns, text="启/禁", command=self.toggle_s)
        btn_toggle.pack(side=tk.LEFT, padx=5)
        ToolTip(btn_toggle, "暂时停止或恢复对选中文件的监控与词库生成功能")
        
        btn_rm = ttk.Button(sf_btns, text="移除", command=self.remove_s)
        btn_rm.pack(side=tk.LEFT, padx=(5, 0))
        ToolTip(btn_rm, "将选中项从列表中彻底移除，停止为其生成独立词库")

        # Output Space (Table)
        o_frame = ttk.LabelFrame(self.home_scrollable_frame, text=" 第二步：设置转换后 QuickKV 词库 (.md) 的保存位置 ", padding="15")
        o_frame.pack(fill=tk.X, pady=10)
        
        o_tree_subframe = ttk.Frame(o_frame)
        o_tree_subframe.pack(fill=tk.X, expand=True, pady=(0, 10))
        
        self.o_tree = ttk.Treeview(o_tree_subframe, columns=("path", "clear", "open"), show="headings", height=1)
        self.o_tree.heading("path", text="当前设定的导出目录与更新机制", anchor='w')
        self.o_tree.heading("clear", text="危险区", anchor='c')
        self.o_tree.heading("open", text="操作", anchor='c')
        self.o_tree.column("path", anchor='w')
        self.o_tree.column("clear", width=120, anchor='c')
        self.o_tree.column("open", width=80, anchor='c')
        
        o_tree_scroll = ttk.Scrollbar(o_tree_subframe, orient="vertical", command=self.o_tree.yview)
        self.o_tree.configure(yscrollcommand=o_tree_scroll.set)
        o_tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.o_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        self.o_tree.bind("<Button-1>", self.on_o_tree_click)
        
        o_btn_frame = ttk.Frame(o_frame)
        o_btn_frame.pack(fill=tk.X)
        btn_out = ttk.Button(o_btn_frame, text="📁 更改导出目录...", command=self.select_o)
        btn_out.pack(side=tk.LEFT)
        ToolTip(btn_out, "设置转换后的词库文件最终要保存在哪里（建议直接选为 QuickKV 的自动载入词库目录）")
        
        btn_rescan = ttk.Button(o_btn_frame, text="🚀 立即全量扫描并重建词库", command=self.confirm_and_queue_rescan)
        btn_rescan.pack(side=tk.RIGHT, padx=5)
        ToolTip(btn_rescan, "强制全盘重新读取一遍所有笔记文档，并覆写生成最新的词库。建议在调整排除规则或更改目录后手动点一次")
        
        cb_auto = ttk.Checkbutton(o_btn_frame, text="后台自动更新 (修改源笔记时自动导出)", variable=self.auto_generate, command=self.toggle_mon)
        cb_auto.pack(side=tk.RIGHT, padx=10)
        ToolTip(cb_auto, "打勾后，您只需在笔记软件里正常修改内容并按下 Ctrl+S 保存，KVT 会在几秒钟内自动帮您无感更新词库数据")
        
        # Results View
        g_frame = ttk.LabelFrame(self.home_scrollable_frame, text=" 最终生成的 QuickKV 词库状态预览 ", padding="15")
        g_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        g_tree_subframe = ttk.Frame(g_frame)
        g_tree_subframe.pack(fill=tk.BOTH, expand=True)
        
        self.g_tree = ttk.Treeview(g_tree_subframe, columns=("output", "name", "source", "path", "action"), show="headings", height=5)
        self.g_tree.heading("output", text="是否导出？", anchor='w')
        self.g_tree.heading("name", text="生成的词库名", anchor='w')
        self.g_tree.heading("source", text="词库数据来源", anchor='w')
        self.g_tree.heading("path", text="已生成文件的真实路径", anchor='w')
        self.g_tree.heading("action", text="操作", anchor='c')
        self.g_tree.column("output", width=80, anchor='c')
        self.g_tree.column("action", width=80, anchor='c')
        self.g_tree.column("source", width=200)
        
        g_tree_scroll = ttk.Scrollbar(g_tree_subframe, orient="vertical", command=self.g_tree.yview)
        self.g_tree.configure(yscrollcommand=g_tree_scroll.set)
        g_tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.g_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.g_tree.bind("<Button-1>", self.on_g_tree_click)
        self._source_tip = None
        self._source_tip_after = None
        self._source_tip_row = None
        self.g_tree.bind('<Motion>', self._hover_output_source)
        self.g_tree.bind('<Leave>', self._hide_source_tip)
        
        g_btn_frame = ttk.Frame(g_frame)
        g_btn_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=5)
        btn_blacklist = ttk.Button(g_btn_frame, text="🚫 词库排除选择 (黑名单)", command=self.manage_blacklist)
        btn_blacklist.pack(side=tk.LEFT, padx=5)
        ToolTip(btn_blacklist, "配置哪些词库标签要被永久剔除，不显示也不生成文件")
        
    def _build_settings_tab(self):
        opts = self.app_state.get_advanced_options()
        rules = self.app_state.get_rules()

        # Split into two columns
        left_col = ttk.Frame(self.tab_settings)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        right_col = ttk.Frame(self.tab_settings)
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))

        # Left: Rules
        rules_lf = ttk.LabelFrame(left_col, text=" 🧹 数据清洗与过滤规则 (高级正则表达式/双元替换) ", padding="15")
        rules_lf.pack(fill=tk.BOTH, expand=True)
        
        info_lbl = ttk.Label(rules_lf, text="配置需要跳过提取的特定文本或正则（每条正则独立）：", style="Muted.TLabel")
        info_lbl.pack(anchor="w", pady=(0, 10))
        
        btn_rules = ttk.Button(rules_lf, text="🪄 管理数据清洗与过滤规则", command=self.manage_rules)
        btn_rules.pack(anchor="w", pady=5)
        ToolTip(btn_rules, "点击打开双元替换清洗引擎配置面板")
        
        # Adding some helper text in the blank space
        helper_text = "提示：\n• 如果你不想提取带有 'test' 的行，可以直接添加 'test'\n• 如果要高级过滤，可填写标准的正则语法"
        ttk.Label(rules_lf, text=helper_text, style="Muted.TLabel", justify=tk.LEFT).pack(anchor="w", pady=(20, 0))

        # Right: Advanced Options
        common_lf = ttk.LabelFrame(right_col, text=" ⚙️ 常规偏好 ", padding="15")
        common_lf.pack(fill=tk.X, pady=(0, 15))
        ttk.Button(common_lf, text='排除文件夹 / 文件路径',
                   command=self.manage_excluded_paths).pack(anchor='w', pady=(0, 8))
        appearance = ttk.Frame(common_lf)
        appearance.pack(anchor="w", pady=(0, 5))
        ttk.Label(appearance, text="外观主题").pack(side=tk.LEFT, padx=(0, 10))
        for label, value in (("亮色", "light"), ("暗色", "dark")):
            ttk.Radiobutton(appearance, text=label, value=value, variable=self.theme_var,
                            command=self._change_theme).pack(side=tk.LEFT, padx=(0, 8))
        
        self.run_on_startup_var = tk.BooleanVar(value=opts.get("run_on_startup", False))
        ttk.Checkbutton(common_lf, text="系统启动时自启", variable=self.run_on_startup_var).pack(anchor="w", pady=5)
        
        self.minimize_to_tray_var = tk.BooleanVar(value=opts.get("minimize_to_tray", True))
        ttk.Checkbutton(common_lf, text="关闭窗口时最小化到系统托盘", variable=self.minimize_to_tray_var).pack(anchor="w", pady=5)

        logseq_lf = ttk.LabelFrame(right_col, text=" 📄 Logseq md属性扫描 ", padding="15")
        logseq_lf.pack(fill=tk.X, pady=(0, 15))
        
        self.scan_keys_var = tk.BooleanVar(value=opts.get("logseq_scan_keys", False))
        ttk.Checkbutton(logseq_lf, text="页内属性键录入为词条", variable=self.scan_keys_var, command=self.save_settings_from_tab).pack(anchor="w", pady=5)
        
        self.scan_values_var = tk.BooleanVar(value=opts.get("logseq_scan_values", False))
        ttk.Checkbutton(logseq_lf, text="页内属性值录入为词条 (带[[]]的)", variable=self.scan_values_var, command=self.save_settings_from_tab).pack(anchor="w", pady=5)
        
        self.scan_pure_values_var = tk.BooleanVar(value=opts.get("logseq_scan_pure_values", False))
        ttk.Checkbutton(logseq_lf, text="页内属性值录入为词条 (无[[]]的纯文本)", variable=self.scan_pure_values_var, command=self.save_settings_from_tab).pack(anchor="w", pady=5)
        
        btn_ls_exclude = ttk.Button(logseq_lf, text="🚫 管理要排除的 Logseq 属性键", command=self.manage_logseq_excludes)
        btn_ls_exclude.pack(anchor="w", pady=(10, 0))
        ToolTip(btn_ls_exclude, "遇到这些属性键时，转换器会直接忽略整行，不提取键也不提取值（例：alias, type）")

        danger_lf = ttk.LabelFrame(right_col, text=" ⚠️ 危险操作区 ", padding="15")
        danger_lf.pack(fill=tk.X, pady=(0, 15))
        
        self.clear_config_var = tk.BooleanVar(value=True)
        self.clear_cache_var = tk.BooleanVar(value=True)
        self.clear_output_var = tk.BooleanVar(value=False)
        
        ttk.Checkbutton(danger_lf, text="清除设置与规则记录 (恢复出厂状态)", variable=self.clear_config_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(danger_lf, text="清除词库解析缓存 (解决由于缓存导致的树形刷新异常)", variable=self.clear_cache_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(danger_lf, text="删除已导出的词库文件 (清空当前输出目录下所有 KVT 词库)", variable=self.clear_output_var).pack(anchor="w", pady=2)
        
        btn_clear = ttk.Button(danger_lf, text="🗑️ 确认清除所选项", command=self.clear_personal_data)
        btn_clear.pack(anchor="w", pady=(10, 0))
        ToolTip(btn_clear, "一键抹除勾选的 KVT 数据。您的源笔记文件【绝对安全】，绝不受影响。")

        btn_frame = ttk.Frame(right_col)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=10)
        
        # Link label for github help
        github_lbl = ttk.Label(btn_frame, text="❓ 帮助与更新 (GitHub)", style="Link.TLabel", cursor="hand2")
        github_lbl.pack(side=tk.LEFT)
        github_lbl.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/EllisMorrow/KV-Tree"))

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
        msg = "这将清除您勾选的个人数据，重置软件。\n\n• 您的源 .md 笔记文件【绝不】受影响。\n• 清除后软件将立即退出，需要您手动重新打开。\n\n确认清除吗？"
        if messagebox.askyesno("⚠️ 危险操作确认", msg, icon='warning'):
            self.app_state.skip_save = True
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
                    self.update_o_table()
                    
                if self.clear_cache_var.get() and os.path.exists(cache_path):
                    os.remove(cache_path)
                if self.clear_output_var.get():
                    import stat
                    for f in self.app_state.get_active_outputs().keys():
                        if os.path.exists(f):
                            try:
                                os.chmod(f, stat.S_IWRITE)
                                os.remove(f)
                            except Exception: pass
            except Exception as e:
                messagebox.showerror("清除失败", f"清除失败: {e}\n\n请手动删除对应文件。")
            else:
                messagebox.showinfo("清除成功", "所选数据已被清除！\n程序即将退出，请手动重新运行。")
            
            self.tray_icon.stop()
            self.file_monitor.stop()
            self.dispatcher.stop()
            self.destroy()

    def load_state_to_ui(self):
        self.update_o_table()
        self.update_lists()

    def update_o_table(self):
        self.o_tree.delete(*self.o_tree.get_children())
        out_path = self.app_state.get_output_path()
        if not out_path or out_path == os.getcwd():
            display_path = "< 未设定，请点击下方更改目录指定输出位置 >"
        else:
            display_path = out_path
        self.o_tree.insert("", "end", iid="output-directory", values=(display_path, "🗑️ 删已有词库", "👁️ 打开"))
        overlaps = out_path and out_path != os.getcwd() and any(
            data.get("type") == "folder" and _is_within_folder(normalize(out_path), normalize(path))
            for path, data in self.app_state.get_source_files().items()
        )
        self.o_tree.configure(height=2 if overlaps else 1)
        if overlaps:
            self.o_tree.tag_configure("warning", foreground="#FFD081" if self.theme_var.get() == "dark" else "#925000")
            self.o_tree.insert("", "end", tags=("warning",),
                               values=("⚠ 导出目录在笔记源内，生成的词库可能被再次扫描。", "", ""))

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

    def on_o_tree_click(self, event):
        if self.o_tree.identify_region(event.x, event.y) != "cell": return
        if self.o_tree.identify_row(event.y) != "output-directory": return
        column_id = self.o_tree.identify_column(event.x)
        if column_id not in ("#2", "#3"): return
        
        if column_id == "#3":
            p = self.app_state.get_output_path()
            if os.path.exists(p): os.startfile(p)
            else: self.show_error("提示", "目录不存在！")
        elif column_id == "#2":
            if messagebox.askyesno("确认删除", "确认要删除输出目录下所有已生成的 KVT 词库吗？\n(您的源笔记绝对安全，这只会删除转换出的词库文件)"):
                import stat
                count = 0
                for f in self.app_state.get_active_outputs().keys():
                    if os.path.exists(f):
                        try:
                            os.chmod(f, stat.S_IWRITE)
                            os.remove(f)
                            count += 1
                        except Exception: pass
                # Let dispatcher re-sync UI logic natively
                self.dispatcher.put_task(("initialize",))
                messagebox.showinfo("删除完毕", f"已成功删除 {count} 个词库文件。")

    def on_g_tree_click(self, event):
        if self.g_tree.identify_region(event.x, event.y) != "cell": return
        column_id = self.g_tree.identify_column(event.x)
        if column_id == "#3":
            self._hover_output_source(event)
            return
        if column_id not in ("#1", "#5"): return
        item_id = self.g_tree.identify_row(event.y)
        if not item_id: return
        
        if column_id == "#1":
            basename = os.path.basename(item_id)
            current_selection = self.app_state.get_output_selection()
            is_checked = current_selection.get(basename, False)
            self.app_state.set_output_selection(basename, not is_checked)
            
            self.dispatcher.put_task(("regenerate_output", item_id))
            self.g_tree.set(item_id, 'output', '☐' if is_checked else '☑')
        elif column_id == "#5":
            if os.path.exists(item_id):
                os.startfile(item_id)
            else:
                self.show_error("提示", "该词库文件尚未生成或已被删除。")

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
                
        # 3. Scan the entire cache for historical/lazy outputs
        # Note: AppState handles cache mapping via ActiveOutputs now, but looking at raw cache gives absolute everything
        for src, entry in self.dispatcher.cache_manager.cache_data.items():
            for out_p in entry.get("outputs", {}).keys():
                all_possible_basenames.add(os.path.basename(out_p))
                
        dialog = BlacklistWindow(self, all_possible_basenames, self.app_state.get_blacklist)
        saved, new_blacklist = dialog.show()
        if saved:
            current = self.app_state.get_blacklist()
            for b in current:
                self.app_state.remove_from_blacklist(b)
            for b in new_blacklist:
                self.app_state.add_to_blacklist(b)
            
            # Immediately queue a re-initialization so dispatcher will delete the files
            self.dispatcher.put_task(("initialize",))

    def manage_rules(self):
        from src.ui.components import DualRuleWindow
        current_rules = self.app_state.get_rules()
        
        dialog = DualRuleWindow(self, rules_dict=current_rules)
        saved_items = dialog.show()
        if saved_items is not None:
            if saved_items != current_rules:
                self.app_state.set_rules(saved_items)
                if messagebox.askyesno("更新", "数据清洗清洗已修改，是否立即全量重建跑一遍新规则？"):
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
        ttk.Button(buttons, text='保存', command=save).pack(side='right')
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
                      padding=10, justify='left', wraplength=800).pack()
            tip.update_idletasks()
            left = min(x, max(0, self.winfo_screenwidth() - tip.winfo_reqwidth()))
            top = y if y + tip.winfo_reqheight() <= self.winfo_screenheight() else y - tip.winfo_reqheight() - 24
            tip.geometry(f'{left:+d}{max(0, top):+d}')
        self._source_tip_after = self.after(450, show)

    def _prepare_list_refresh(self):
        # Runs on the dispatcher thread. Hover only reads the published index.
        by_source = self.dispatcher.cache_manager.get_source_output_index()
        by_output = {}
        for path, data in self.app_state.get_source_files().items():
            if not data.get('enabled'):
                continue
            entries = data.get('files', {}) if data.get('type') == 'folder' else (path,)
            for entry in entries:
                for output in by_source.get(normalize(entry), ()):
                    by_output.setdefault(os.path.basename(output), set()).add(path)
        self._on_ui_thread(self._apply_list_refresh)(by_output)

    def _apply_list_refresh(self, by_output):
        self._output_sources = by_output
        self.update_lists()

    def update_generated_list(self):
        if hasattr(self, '_source_tip'):
            self._hide_source_tip()
        self.g_tree.delete(*self.g_tree.get_children())
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
            self.g_tree.insert("", "end", iid=f_path, values=(check_char, basename, display_source, f_path, "👁️ 打开"))

    def _restart_monitor_if_auto(self):
        if self.auto_generate.get():
            self.file_monitor.start()

    def update_source_list(self):
        self.update_o_table()
        self.s_tree.delete(*self.s_tree.get_children())
        sources = self.app_state.get_source_files()
        for p, d in sources.items():
            display_text = f"[{d.get('type', 'file').upper()}] {p}"
            self.s_tree.insert("", "end", iid=p, values=(display_text, "启用" if d.get("enabled") else "禁用", "👁️ 打开"))

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
        if folder_path: folder_path = os.path.normpath(folder_path)
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
        
        # open the file or its folder
        path_to_open = item_id
        if os.path.isfile(path_to_open):
            path_to_open = os.path.dirname(path_to_open)
            
        if os.path.exists(path_to_open):
            os.startfile(path_to_open)
        else:
            self.show_error("提示", "该路径已经不存在于磁盘中。")

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
            self.update_o_table()
            self.dispatcher.put_task(("full_rescan",))

    def toggle_mon(self):
        is_auto = self.auto_generate.get()
        opts = self.app_state.get_advanced_options()
        opts["auto_generate"] = is_auto
        self.app_state.update_advanced_options(opts)
        
        if is_auto: self.file_monitor.start()
        else: self.file_monitor.stop()

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
