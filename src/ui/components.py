import re
import tkinter as tk
from tkinter import messagebox, ttk

from src.logic.ast_parser import compile_rule

class ScrollablePane(ttk.Frame):
    """A vertically scrolling surface scoped to the window that owns it."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)
        window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.body.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width))
        self.winfo_toplevel().bind("<MouseWheel>", self._scroll, add="+")
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

    def _update_scroll_region(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _scroll(self, event):
        if not self.winfo_ismapped():
            return
        widget = event.widget
        while widget is not self and widget is not None:
            if isinstance(widget, tk.Text):
                return
            if isinstance(widget, ttk.Treeview) and widget.yview() != (0.0, 1.0):
                return
            widget = widget.master
        if widget is self and event.delta and self.canvas.yview() != (0.0, 1.0):
            steps = max(1, abs(event.delta) // 120)
            self.canvas.yview_scroll(-steps if event.delta > 0 else steps, "units")
            return "break"


class ToolTip(object):
    """
    Creates a ToolTip (hover-box) for any tkinter widget.
    """
    def __init__(self, widget, text='widget info'):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        self.id = None
        self.widget.bind("<Enter>", self.enter, add="+")
        self.widget.bind("<Leave>", self.leave, add="+")
        self.widget.bind("<Destroy>", self.leave, add="+")

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hidetip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(500, self.showtip)

    def unschedule(self):
        id_ = self.id
        self.id = None
        if id_:
            self.widget.after_cancel(id_)

    def showtip(self, event=None):
        self.unschedule()
        if self.tipwindow or not self.widget.winfo_ismapped():
            return
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tipwindow = tip = tk.Toplevel(self.widget)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        ttk.Label(tip, text=self.text, style="Tooltip.TLabel",
                  justify=tk.LEFT, padding=(10, 8), wraplength=320).pack()
        tip.update_idletasks()
        x = min(x, self.widget.winfo_screenwidth() - tip.winfo_reqwidth())
        if y + tip.winfo_reqheight() > self.widget.winfo_screenheight():
            y = self.widget.winfo_rooty() - tip.winfo_reqheight() - 6
        tip.geometry(f"{max(x, 0):+d}{max(y, 0):+d}")

    def hidetip(self):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()

class DynamicListWindow(tk.Toplevel):
    NUM_COLUMNS = 2  # 两列布局
    
    def __init__(self, parent, title, instruction, initial_items, placeholder="在这里输入匹配内容..."):
        super().__init__(parent)
        self.title(title)
        self.geometry("750x550")
        self._center_window(parent)
        self.transient(parent)
        self.grab_set()
        self.bind("<Escape>", lambda event: self.destroy())
        
        self.placeholder = placeholder
        self.rows = []  # [(row_frame, entry_var, badge_lbl), ...]
        self._grid_row_idx = 0  # grid 行计数器
        
        # Header Info
        info_lbl = ttk.Label(self, text=instruction, style="Muted.TLabel", justify=tk.LEFT, wraplength=710)
        info_lbl.pack(anchor="w", padx=15, pady=(15, 2))
        
        # 前缀匹配说明
        prefix_info = ttk.Label(self, text='💡 提示：条目末尾加 * 表示前缀匹配。例如 card-* 会匹配 card-last-reviewed、card-repeats 等所有以 card- 开头的键。', 
                                foreground="#0078D4", justify=tk.LEFT, wraplength=710, font=("", 9))
        prefix_info.pack(anchor="w", padx=15, pady=(0, 8))
        
        # ===== 搜索栏和工具按钮  =====
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, padx=15, pady=(0, 5))
        
        ttk.Label(toolbar, text="搜索", style="Muted.TLabel").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_changed)
        search_entry = ttk.Entry(toolbar, textvariable=self.search_var, font=("Consolas", 10))
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 10))
        
        self.match_count_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.match_count_var, style="Muted.TLabel").pack(side=tk.LEFT, padx=(0, 10))
        
        btn_sort = ttk.Button(toolbar, text="按名称排序", command=self._sort_alphabetically)
        btn_sort.pack(side=tk.RIGHT)
        ToolTip(btn_sort, "按首字母对所有条目进行升序排列（中文按拼音）")
        
        pane = ScrollablePane(self)
        pane.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)
        self.canvas, self.scrollable_frame = pane.canvas, pane.body
        for column in range(self.NUM_COLUMNS):
            self.scrollable_frame.columnconfigure(column, weight=1, uniform="entries")

        # 加载初始条目（先去重再排序）
        unique_items = list(dict.fromkeys(item.strip() for item in initial_items if item.strip()))
        unique_items = self._sort_values(unique_items)
        for item in unique_items:
            self.add_row(item)
                
        # 如果没有条目，添加一个空行
        if not self.rows:
            self.add_row("")

        # ===== 底部按钮栏 =====
        bottom_frame = ttk.Frame(self)
        bottom_frame.pack(fill=tk.X, padx=15, pady=(5, 10))
        
        btn_add = ttk.Button(bottom_frame, text="添加条目", command=self._add_empty_row)
        btn_add.pack(side=tk.LEFT)
        
        btn_frame_right = ttk.Frame(bottom_frame)
        btn_frame_right.pack(side=tk.RIGHT)
        ttk.Button(btn_frame_right, text="保存", style="Accent.TButton", command=self.save_and_close).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame_right, text="取消", command=self.destroy).pack(side=tk.LEFT, padx=5)
        
        self.saved_items = None
        self._update_match_count()
        search_entry.focus_set()
        
    @staticmethod
    def _sort_key(text):
        """排序键：中文按拼音首字母排序，英文按小写字母"""
        text = text.lower().strip()
        try:
            from pypinyin import lazy_pinyin
            return lazy_pinyin(text)
        except ImportError:
            # 没有 pypinyin 库则退回到普通排序
            return [text]
    
    @staticmethod
    def _sort_values(values):
        """对值列表排序并去重"""
        try:
            from pypinyin import lazy_pinyin
            return sorted(values, key=lambda x: lazy_pinyin(x.lower().strip()))
        except ImportError:
            return sorted(values, key=lambda x: x.lower().strip())
        
    def _add_empty_row(self):
        self.search_var.set("")
        entry = self.add_row()
        self._update_match_count()
        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1.0)
        entry.focus_set()

    def add_row(self, content=""):
        # 计算当前应在 grid 的哪一行哪一列
        total = len(self.rows)
        grid_row = total // self.NUM_COLUMNS
        grid_col = total % self.NUM_COLUMNS
        
        row_frame = ttk.Frame(self.scrollable_frame)
        row_frame.grid(row=grid_row, column=grid_col, sticky="ew", pady=2, padx=3)
        
        entry_var = tk.StringVar(value=content)
        
        # 前缀匹配标记标签
        badge_lbl = ttk.Label(row_frame, text="", width=4, anchor="c", font=("", 8))
        badge_lbl.pack(side=tk.LEFT, padx=(0, 2))
        
        entry = ttk.Entry(row_frame, textvariable=entry_var, font=("Consolas", 10))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        
        btn_rm = ttk.Button(row_frame, text="➖", width=3, command=lambda: self.remove_row(row_frame, entry_var))
        btn_rm.pack(side=tk.RIGHT)
        
        self.rows.append((row_frame, entry_var, badge_lbl))
        
        # 实时更新前缀标记
        def _update_badge(*_):
            val = entry_var.get().strip()
            if val.endswith("*"):
                badge_lbl.configure(text="前缀", style="Prefix.TLabel")
            else:
                badge_lbl.configure(text="精确" if val else "", style="Muted.TLabel")
        
        entry_var.trace_add("write", _update_badge)
        _update_badge()  # 初始化显示
        
        return entry
        
    def remove_row(self, frame, var):
        frame.grid_forget()
        frame.destroy()
        self.rows = [(f, v, b) for f, v, b in self.rows if v != var]
        self._reflow_grid()
        self._on_search_changed()
    
    def _reflow_grid(self):
        """删除行后重新排列 grid 布局"""
        for idx, (row_frame, _, _) in enumerate(self.rows):
            grid_row = idx // self.NUM_COLUMNS
            grid_col = idx % self.NUM_COLUMNS
            row_frame.grid(row=grid_row, column=grid_col, sticky="ew", pady=2, padx=3)
        
    def _on_search_changed(self, *_):
        """实时搜索过滤：隐藏不匹配的行"""
        keyword = self.search_var.get().strip().lower()
        visible = 0
        for row_frame, entry_var, _ in self.rows:
            val = entry_var.get().strip().lower()
            if not keyword or keyword in val:
                row_frame.grid()  # 恢复显示
                visible += 1
            else:
                row_frame.grid_remove()  # 隐藏但保留位置
        self._update_match_count(visible_override=visible)
    
    def _update_match_count(self, visible_override=None):
        total = len(self.rows)
        if visible_override is not None:
            self.match_count_var.set(f"显示 {visible_override}/{total}")
        else:
            self.match_count_var.set(f"共 {total} 条")
    
    def _sort_alphabetically(self):
        """按首字母对所有条目进行 A→Z 升序排列（中文按拼音）"""
        # 收集所有条目的值并去重
        values = list(dict.fromkeys(var.get().strip() for _, var, _ in self.rows if var.get().strip()))
        values = self._sort_values(values)
        
        # 清空所有行的 UI
        for row_frame, _, _ in self.rows:
            row_frame.grid_forget()
            row_frame.destroy()
        self.rows.clear()
        
        # 重新按排序后的顺序创建
        for val in values:
            self.add_row(val)
        if not self.rows:
            self.add_row("")
            
        # 清空搜索框
        self.search_var.set("")
        self._update_match_count()
        
    def show(self):
        self.wait_window()
        return self.saved_items
        
    def save_and_close(self):
        # 保存时自动去重
        seen = set()
        items = []
        for _, var, _ in self.rows:
            val = var.get().strip()
            if val and val not in seen:
                items.append(val)
                seen.add(val)
        self.saved_items = items
        self.destroy()

    def _center_window(self, parent):
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

class DualRuleWindow(tk.Toplevel):
    def __init__(self, parent, rules_dict=None):
        super().__init__(parent)
        self.title("数据清洗与过滤规则")
        self.geometry("750x650")
        self.transient(parent)
        self.grab_set()
        self.bind("<Escape>", lambda event: self.destroy())
        self._center_window(parent)
        
        self.result = None
        
        main_frame = ttk.Frame(self, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        pane = ScrollablePane(main_frame)
        pane.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
        self.scrollable_frame = pane.body

        self.line_rows = []
        self.content_rows = []

        if not rules_dict or not isinstance(rules_dict, dict):
            rules_dict = {"line_rules": [], "content_rules": []}

        # --- SECTION 1: Line Rules ---
        self._build_section(
            self.scrollable_frame, 
            "排除行", "匹配的整行将被排除。填写替换项时，保留替换后的行。",
            self.line_rows, 
            rules_dict.get("line_rules", [])
        )

        ttk.Separator(self.scrollable_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=20)

        # --- SECTION 2: Content Rules ---
        self._build_section(
            self.scrollable_frame, 
            "排除内容", "仅删除或替换行内匹配的片段。",
            self.content_rows, 
            rules_dict.get("content_rules", [])
        )

        # Bottom Actions
        action_frame = ttk.Frame(main_frame)
        action_frame.pack(fill=tk.X, side=tk.BOTTOM)
        
        btn_save = ttk.Button(action_frame, text="保存规则", style="Accent.TButton", command=self.save_and_close)
        btn_save.pack(side=tk.RIGHT)
        
        btn_cancel = ttk.Button(action_frame, text="取消", command=self.destroy)
        btn_cancel.pack(side=tk.RIGHT, padx=10)

    def _build_section(self, parent_frame, title, description, row_list, initial_data):
        section_frame = ttk.Frame(parent_frame)
        section_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(section_frame, text=title, style="Section.TLabel").pack(anchor="w")
        ttk.Label(section_frame, text=description, style="Muted.TLabel").pack(anchor="w", pady=(2, 10))
        
        col_frame = ttk.Frame(section_frame)
        col_frame.pack(fill=tk.X)
        ttk.Label(col_frame, text="欲匹配的内容 (正则或文本)").pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Label(col_frame, text="替换项 (没有则直接为空)").pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(10,0))
        ttk.Label(col_frame, text="操作", width=10).pack(side=tk.RIGHT)
        
        list_frame = ttk.Frame(section_frame)
        list_frame.pack(fill=tk.X, pady=5)
        
        if not initial_data:
            for _ in range(2):
                self._add_row_to(list_frame, row_list)
        else:
            for item in initial_data:
                self._add_row_to(list_frame, row_list, item.get("match", ""), item.get("replace", ""))
                
        btn_add = ttk.Button(section_frame, text="➕ 添加一行", command=lambda f=list_frame, r=row_list: self._add_row_to(f, r))
        btn_add.pack(anchor="w", pady=5)

    def _add_row_to(self, parent_frame, row_list, init_match="", init_replace=""):
        row_frame = ttk.Frame(parent_frame)
        row_frame.pack(fill=tk.X, pady=2)
        
        m_var = tk.StringVar(value=init_match)
        r_var = tk.StringVar(value=init_replace)
        
        e_m = ttk.Entry(row_frame, textvariable=m_var, font=("Consolas", 10))
        e_m.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        e_r = ttk.Entry(row_frame, textvariable=r_var, font=("Consolas", 10))
        e_r.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        
        btn_rm = ttk.Button(row_frame, text="➖ 删除", width=6)
        btn_rm.config(command=lambda f=row_frame, t=(m_var, r_var), l=row_list: self._remove_row(f, t, l))
        btn_rm.pack(side=tk.RIGHT)
        
        row_list.append((m_var, r_var))
        
    def _remove_row(self, frame, row_tuple, row_list):
        frame.destroy()
        if row_tuple in row_list:
            row_list.remove(row_tuple)
            
    def save_and_close(self):
        l_rules = [{"match": m.get().strip(), "replace": r.get()} for m, r in self.line_rows if m.get().strip()]
        c_rules = [{"match": m.get().strip(), "replace": r.get()} for m, r in self.content_rows if m.get().strip()]
                
        for label, rules in (("排除行", l_rules), ("排除内容", c_rules)):
            for number, rule in enumerate(rules, 1):
                try:
                    compile_rule(rule)
                except (re.error, IndexError, OverflowError) as error:
                    messagebox.showerror("规则无效", f"{label}第 {number} 条：{error}", parent=self)
                    return
        self.result = {"line_rules": l_rules, "content_rules": c_rules}
        self.destroy()
        
    def show(self):
        self.wait_window()
        return self.result

    def _center_window(self, parent):
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

class AdvancedOptionsWindow(tk.Toplevel):
    def __init__(self, parent, options):
        super().__init__(parent)
        self.title("高级选项")
        self.geometry("400x320")
        self._center_window(parent)
        self.transient(parent)
        self.grab_set()
        self.bind("<Escape>", lambda event: self.destroy())
        
        self.options = options
        self.scan_keys_var = tk.BooleanVar(value=options.get("logseq_scan_keys", False))
        self.scan_values_var = tk.BooleanVar(value=options.get("logseq_scan_values", False))
        self.scan_pure_values_var = tk.BooleanVar(value=options.get("logseq_scan_pure_values", False))
        self.run_on_startup_var = tk.BooleanVar(value=options.get("run_on_startup", False))
        self.minimize_to_tray_var = tk.BooleanVar(value=options.get("minimize_to_tray", True))
        
        notebook = ttk.Notebook(self)
        notebook.pack(padx=10, pady=10, fill="both", expand=True)
        
        common_frame = ttk.Frame(notebook)
        notebook.add(common_frame, text="常用")
        common_lf = ttk.LabelFrame(common_frame, text="常规设置", padding="10")
        common_lf.pack(padx=10, pady=10, fill="x")
        ttk.Checkbutton(common_lf, text="系统启动时启动", variable=self.run_on_startup_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(common_lf, text="最小化时在托盘 (默认勾选)", variable=self.minimize_to_tray_var).pack(anchor="w", pady=2)
        
        scan_frame = ttk.Frame(notebook)
        notebook.add(scan_frame, text="扫描")
        logseq_lf = ttk.LabelFrame(scan_frame, text="Logseq md属性扫描", padding="10")
        logseq_lf.pack(padx=10, pady=10, fill="x")
        ttk.Checkbutton(logseq_lf, text="页内属性键录入为词条", variable=self.scan_keys_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(logseq_lf, text="页内属性值录入为词条-带双方括号[[]]的", variable=self.scan_values_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(logseq_lf, text="页内属性值录入为词条 (无[[]]的纯文本)", variable=self.scan_pure_values_var).pack(anchor="w", pady=2)
        
        button_frame = ttk.Frame(self)
        button_frame.pack(pady=10)
        ttk.Button(button_frame, text="保存", command=self.save_and_close).pack(side="left", padx=5)
        ttk.Button(button_frame, text="取消", command=self.destroy).pack(side="left", padx=5)
        
        self.saved_options = None
        
    def save_and_close(self):
        self.saved_options = {
            "logseq_scan_keys": self.scan_keys_var.get(),
            "logseq_scan_values": self.scan_values_var.get(),
            "logseq_scan_pure_values": self.scan_pure_values_var.get(),
            "run_on_startup": self.run_on_startup_var.get(),
            "minimize_to_tray": self.minimize_to_tray_var.get()
        }
        self.destroy()
        
    def show(self):
        self.wait_window()
        return self.saved_options

    def _center_window(self, parent):
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

class BlacklistWindow(tk.Toplevel):
    def __init__(self, parent, all_possible_basenames, blacklist_manager):
        super().__init__(parent)
        self.title("词库黑名单")
        self.geometry("450x450")
        self._center_window(parent)
        self.transient(parent)
        self.grab_set()
        self.bind("<Escape>", lambda event: self.destroy())
        
        self.blacklist_manager = blacklist_manager
        current_blacklist = self.blacklist_manager()
        
        # Combine possible basenames with anything historically in the blacklist
        all_known_basenames = set(current_blacklist)
        for basename in all_possible_basenames:
            all_known_basenames.add(basename)
            
        self.vars = {}
        
        info_lbl = ttk.Label(self, text="勾选的词库将从预览隐藏，并删除对应的导出文件。", style="Muted.TLabel", justify=tk.LEFT, wraplength=400)
        info_lbl.pack(anchor="w", padx=15, pady=(15, 5))
        
        pane = ScrollablePane(self)
        pane.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)
        self.scrollable_frame = pane.body

        if not all_known_basenames:
            ttk.Label(self.scrollable_frame, text="暂无发现任何生成过的词库...").pack(pady=20)
            
        for basename in sorted(list(all_known_basenames)):
            var = tk.BooleanVar(value=(basename in current_blacklist))
            self.vars[basename] = var
            cb = ttk.Checkbutton(self.scrollable_frame, text=basename, variable=var)
            cb.pack(anchor="w", pady=2)

        button_frame = ttk.Frame(self)
        button_frame.pack(pady=10)
        ttk.Button(button_frame, text="保存黑名单", style="Accent.TButton", command=self.save_and_close).pack(side="left", padx=5)
        ttk.Button(button_frame, text="取消", command=self.destroy).pack(side="left", padx=5)
        
        self.saved = False
        self.final_blacklist = current_blacklist

    def save_and_close(self):
        new_list = set()
        for basename, var in self.vars.items():
            if var.get():
                new_list.add(basename)
        self.final_blacklist = new_list
        self.saved = True
        self.destroy()

    def show(self):
        self.wait_window()
        return self.saved, self.final_blacklist

    def _center_window(self, parent):
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
