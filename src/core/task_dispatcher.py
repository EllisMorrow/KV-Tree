import queue
import threading
import os
import stat
import time
import concurrent.futures
from src.logic.ast_parser import AstParser
from src.logic.cache_manager import PARSER_REVISION
from src.logic.path_filter import is_excluded, normalize, scan_markdown
from src.logic.file_monitor import _is_within_folder
from src.logic.logseq_parser import LogseqParser
from src.utils.file_utils import atomic_write

class TaskDispatcher:
    def __init__(self, app_state, cache_manager, ui_callbacks):
        self.state = app_state
        self.cache_manager = cache_manager
        self.ui_cb = ui_callbacks # dict: set_status, update_progress, update_lists, prompt_confirm, etc
        
        self.parser = AstParser()
        self.logseq_parser = None
        
        self.task_queue = queue.Queue()
        self.worker_thread = None
        self.dirty_files = set()
        self._failed_exports = set()
        self._pending_event_paths = set()
        self._pending_event_lock = threading.Lock()
        self.last_dirty_time = 0
        self.first_dirty_time = 0
        self.debounce_seconds = 2.0
        self.max_batch_wait = 10.0
        self.running = True
        
    def start(self):
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        
    def stop(self):
        self.running = False
        self.task_queue.put(("exit",))
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join()
            
    def put_task(self, task):
        if task[0] == "process_file":
            path = task[2]
            with self._pending_event_lock:
                if path in self._pending_event_paths:
                    return
                self._pending_event_paths.add(path)
                self.task_queue.put(task)
        else:
            self.task_queue.put(task)
        
    def _worker_loop(self):
        while self.running:
            now = time.monotonic()
            if self.dirty_files and (
                now - self.last_dirty_time >= self.debounce_seconds
                or now - self.first_dirty_time >= self.max_batch_wait
            ):
                try:
                    self._process_dirty_batch()
                except Exception as error:
                    print(f"Batch update error: {error}")
                    self.ui_cb['set_status']("本批更新失败，监控仍在运行。可执行全量重建。")
            try:
                task = self.task_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                task_name = task[0]
                
                if task_name == "exit": 
                    break
                elif task_name == "initialize": 
                    self._execute_initialize()
                elif task_name == "scan_folder": 
                    self._execute_scan_folder(task[1])
                elif task_name == "process_file":
                    with self._pending_event_lock:
                        self._pending_event_paths.discard(task[2])
                    self.last_dirty_time = time.monotonic()
                    if not self.dirty_files:
                        self.first_dirty_time = self.last_dirty_time
                    # Add to dirty set instead of immediate processing
                    self.dirty_files.add((task[1], task[2])) # (event_type, path)
                elif task_name == "regenerate_output": 
                    self._execute_regenerate_output(task[1])
                elif task_name == "full_rescan": 
                    self._execute_full_rescan()
                elif task_name == "clear_cache": 
                    self._execute_clear_cache()
                
            except Exception as e:
                print(f"Worker thread error: {e}")
            finally:
                self.task_queue.task_done()
                
    def _process_dirty_batch(self):
        unique_paths = {path for _, path in self.dirty_files}
        self.dirty_files.clear()
        
        self.ui_cb['set_status'](f"后台批量处理 {len(unique_paths)} 个文件...")
        dirty_outputs = set()
        
        rules = self.state.get_rules()
        adv_opts = self.state.get_advanced_options()
        output_path_base = self.state.get_output_path()
        logseq_exclude_keys = self.state.get_logseq_exclude_keys()
        
        enabled_scope = self._get_enabled_event_paths(unique_paths)
        failed = 0
        
        for path in unique_paths:
            if path not in enabled_scope:
                continue
            is_deleted = not os.path.exists(path)
            old, new = self._update_cache_for_file(path, deleted=is_deleted,
                                                    rules=rules, adv_opts=adv_opts,
                                                    output_path_base=output_path_base,
                                                    logseq_exclude_keys=logseq_exclude_keys)
            if new is None:
                failed += 1
                continue
            dirty_outputs.update(old.keys())
            dirty_outputs.update(new.keys())
            
        dirty_outputs.update(self._failed_exports)
        export_errors = [error for path in dirty_outputs
                         if (error := self._update_single_output_file(path))]
        self.cache_manager.save_cache()
        self.ui_cb['update_lists'](dirty_outputs)
        if export_errors:
            source_error = f"另有 {failed} 个源文件读取失败，已保留上次结果。" if failed else ""
            self.ui_cb['set_status'](f"批量更新有 {len(export_errors)} 项导出失败。{export_errors[0]}{source_error}")
        elif failed:
            self.ui_cb['set_status'](f"批量更新完成，{failed} 个源文件读取失败，已保留上次结果。")
        else:
            self.ui_cb['set_status']("批量更新完成。")
        
    def _execute_initialize(self, force=False):
        self.ui_cb['set_status']("极速启动：正在多线程校验缓存和解析文件...")
        self.ui_cb['update_progress'](mode='determinate', val=0)
        
        # 检查输出路径是否与缓存中的路径一致，如果不一致则强制重建
        current_output = self.state.get_output_path()
        cached_paths_list = self.cache_manager.get_all_cached_paths()
        if cached_paths_list:
            # 检查第一个缓存条目的输出路径是否包含当前 output_path
            sample_outputs = self.cache_manager.get_outputs_for_file(cached_paths_list[0])
            if sample_outputs:
                sample_key = next(iter(sample_outputs))
                # 如果当前输出路径为空或者缓存中的路径前缀不匹配，强制清除所有缓存的 mtime 以触发重建
                if (not current_output or current_output == os.getcwd()) or \
                   (current_output and not sample_key.startswith(current_output)):
                    for cp in cached_paths_list:
                        entry = self.cache_manager.get_entry(cp)
                        if entry:
                            entry['mtime'] = 0  # 强制使 mtime 失效，触发重新解析
                            self.cache_manager.update_entry(cp, 0, entry.get('outputs', {}))
        
        all_source_files = set(self._get_all_source_files())
        dirty_outputs = set()
        cached_paths = self.cache_manager.get_all_cached_paths()
        total_files = len(all_source_files)
        
        files_to_update = {}
        
        # 1. Quick initial sync and filter
        for i, file_path in enumerate(all_source_files):
            if i % 100 == 0:
                self.ui_cb['set_status'](f"对比文件时间戳 ({i}/{total_files})...")
                self.ui_cb['update_progress'](val=(i/total_files*50) if total_files > 0 else 0)
            
            try:
                current_mtime = os.path.getmtime(file_path)
            except OSError as error:
                print(f"Cannot read source timestamp {file_path}: {error}")
                continue
            
            cached_entry = self.cache_manager.get_entry(file_path)
            if (force or not cached_entry or cached_entry.get("mtime") != current_mtime
                    or cached_entry.get("parser_revision") != PARSER_REVISION):
                files_to_update[file_path] = current_mtime
                
        # 2. Parallel Processing
        if files_to_update:
            total_updates = len(files_to_update)
            self.ui_cb['set_status'](f"启用多核并发引擎解析 {total_updates} 个变动文件...")
            
            rules = self.state.get_rules()
            adv_opts = self.state.get_advanced_options()
            output_path_base = self.state.get_output_path()
            logseq_exclude_keys = self.state.get_logseq_exclude_keys()

            completed = 0
            with concurrent.futures.ThreadPoolExecutor() as executor:
                # Map paths to future logic manually to handle cache manager locking
                tasks = {
                    executor.submit(
                        self._parse_single_file_stateless,
                        path, rules, adv_opts, output_path_base, logseq_exclude_keys
                    ): path for path in files_to_update
                }
                
                for future in concurrent.futures.as_completed(tasks):
                    path = tasks[future]
                    try:
                        new_data = future.result()
                        # Synchronous cache update
                        old = self.cache_manager.get_outputs_for_file(path)
                        # A concurrent save must not mark old parsed content as current.
                        self.cache_manager.update_entry(path, files_to_update[path], new_data)
                        dirty_outputs.update(old.keys())
                        dirty_outputs.update(new_data.keys())
                    except Exception as exc:
                        print(f'{path} generated an exception: {exc}')
                    
                    completed += 1
                    if completed % 50 == 0 or completed == total_updates:
                        self.ui_cb['set_status'](f"深度多核解析中 ({completed}/{total_updates})...")
                        self.ui_cb['update_progress'](val=50 + (completed/total_updates*40))
        
        self.ui_cb['set_status']("正在清理失效缓存...")
        for file_path in cached_paths:
            if file_path not in all_source_files or not os.path.exists(file_path):
                old = self.cache_manager.get_outputs_for_file(file_path)
                self.cache_manager.remove_entry(file_path)
                dirty_outputs.update(old.keys())

        dirty_outputs.update(self._failed_exports)
        export_errors = []
        if dirty_outputs:
            total_outputs = len(dirty_outputs)
            for i, out_path in enumerate(dirty_outputs):
                self.ui_cb['set_status'](f"更新输出({i+1}/{total_outputs}): {os.path.basename(out_path)}")
                self.ui_cb['update_progress'](val=(i+1)/total_outputs*100)
                error = self._update_single_output_file(out_path)
                if error:
                    export_errors.append(error)
        
        # update active outputs state — 根据当前 output_path 过滤
        outputs_map = {}
        current_out = self.state.get_output_path()
        virtual_prefix = "<仅缓存,未设输出目录>"
        for src in self.cache_manager.get_all_cached_paths():
            for p in self.cache_manager.get_outputs_for_file(src):
                basename = os.path.basename(p)
                if not current_out or current_out == os.getcwd():
                    # 输出路径未设置，使用虚拟前缀展示
                    outputs_map[os.path.join(virtual_prefix, basename)] = "多元"
                elif p.startswith(current_out):
                    outputs_map[p] = "多元"
                else:
                    # 路径与当前输出不匹配，用当前路径重建显示
                    outputs_map[os.path.join(current_out, basename)] = "多元"
        self.state.set_active_outputs(outputs_map)
        
        self.cache_manager.save_cache()
        self.ui_cb['update_lists']()
        self.ui_cb['set_status'](
            f"有 {len(export_errors)} 项导出失败。{export_errors[0]}" if export_errors else "准备就绪。"
        )
        self.ui_cb['update_progress'](val=0)

    def _execute_scan_folder(self, folder_path):
        self.ui_cb['set_status'](f"正在后台扫描: {folder_path}...")
        self.ui_cb['update_progress'](mode='determinate', val=0)
        
        self.ui_cb['set_status'](f"正在建立极速索引: {folder_path}...")
        scanned_files, errors = scan_markdown(folder_path, self.state.get_excluded_paths())
        if errors:
            self.ui_cb['show_error']('部分目录无法扫描', '\n'.join(str(error) for error in errors[:5]))
        
        total_scan = len(scanned_files)
        # Dummy progress update since we already got all mtimes cleanly
        if total_scan > 0:
            self.ui_cb['update_progress'](val=100)
        else:
            self.ui_cb['update_progress'](val=0)
        
        self.ui_cb['update_progress'](val=0)
        # Notify UI to ask user
        self.ui_cb['folder_scanned'](folder_path, scanned_files)

    def _execute_regenerate_output(self, output_path):
        self.ui_cb['set_status'](f"后台更新: {os.path.basename(output_path)}...")
        error = self._update_single_output_file(output_path)
        self.ui_cb['update_lists']({output_path})
        self.ui_cb['set_status'](error or f"'{os.path.basename(output_path)}' 更新完成。")

    def _execute_full_rescan(self):
        self.ui_cb['set_status']("开始全量重建...")
        self._execute_initialize(force=True)
        
    def _execute_clear_cache(self):
        self.ui_cb['set_status']("正在重新解析并更新缓存...")
        try:
            # Keep old output paths until reconciliation. They identify obsolete
            # exports, and retain the last successful result if a source fails.
            self._execute_initialize(force=True)
        except Exception as e:
            self.ui_cb['show_error']("清除缓存失败", str(e))

    def _update_cache_for_file(self, path, deleted=False, rules=None, adv_opts=None, output_path_base=None, logseq_exclude_keys=None):
        old = self.cache_manager.get_outputs_for_file(path)
        new = {}
        if deleted: self.cache_manager.remove_entry(path)
        else:
            if not os.path.exists(path): return old, new
            
            # If rules/options are not provided, fetch them from state (for non-batch updates)
            if rules is None: rules = self.state.get_rules()
            if adv_opts is None: adv_opts = self.state.get_advanced_options()
            if output_path_base is None: output_path_base = self.state.get_output_path()
            if logseq_exclude_keys is None: logseq_exclude_keys = self.state.get_logseq_exclude_keys()

            try:
                mtime = os.path.getmtime(path)
                new = self._parse_single_file_stateless(path, rules, adv_opts, output_path_base, logseq_exclude_keys)
            except Exception as e:
                print(f"Error parsing {path}: {e}")
                # None is distinct from a valid empty parse result. The batch
                # caller skips output regeneration, preserving cache and files.
                return old, None
            self.cache_manager.update_entry(path, mtime, new)
        return old, new

    @staticmethod
    def _parse_single_file_stateless(file_path: str, rules: list, adv_opts: dict, output_path_base: str, logseq_exclude_keys: set) -> dict:
        """
        Pure function for parsing a single file. Safe to run in a thread pool.

        Parsing errors intentionally propagate so callers can preserve the last
        successful cache instead of confusing a failure with a valid empty result.
        """
        outputs = {}
        with open(file_path, "r", encoding="utf-8") as f: content = f.read()
        # Instantiate fresh parsers to avoid thread state corruption
        parser = AstParser()
        res, _ = parser.parse(content, rules=rules)

        for lib, entries in res.items(): outputs[os.path.join(output_path_base, lib)] = "\n".join(entries)

        if adv_opts.get("logseq_scan_keys") or adv_opts.get("logseq_scan_values") or adv_opts.get("logseq_scan_pure_values"):
            logseq_parser = LogseqParser(
                scan_keys=adv_opts.get("logseq_scan_keys", False),
                scan_values=adv_opts.get("logseq_scan_values", False),
                scan_pure_values=adv_opts.get("logseq_scan_pure_values", False),
                exclude_keys=logseq_exclude_keys
            )
            logseq_res = logseq_parser.parse_file_content(content)
            if logseq_res:
                out_path = os.path.join(output_path_base, "Logseq属性键值.md")
                existing = set(outputs.get(out_path, "").splitlines()); existing.update(logseq_res)
                outputs[out_path] = "\n".join(sorted(list(existing)))
        return outputs

    @staticmethod
    def _is_output_path_within_root(output_path, output_root):
        if not output_path or not output_root:
            return False
        try:
            root = os.path.normcase(os.path.realpath(os.path.abspath(output_root)))
            target = os.path.normcase(os.path.realpath(os.path.abspath(output_path)))
            return target != root and os.path.commonpath((root, target)) == root
        except (OSError, ValueError):
            # ValueError also covers different Windows drives.
            return False

    def _update_single_output_file(self, output_path):
        try:
            self._write_output(output_path)
        except OSError as error:
            message = f"导出 {os.path.basename(output_path)} 失败：{error.strerror or error}"
            self._failed_exports.add(output_path)
            return message
        self._failed_exports.discard(output_path)
        return None

    def _write_output(self, output_path):
        all_lines = {
            line for content in self.cache_manager.get_output_contents(output_path)
            for line in content.splitlines()
        }
        basename = os.path.basename(output_path)
        preview_path = os.path.join("<仅缓存,未设输出目录>", basename)
        base_dest = self.state.get_output_path()
        can_export = base_dest and base_dest != os.getcwd()
        if not all_lines:
            self.state.remove_active_output(output_path)
            self.state.remove_active_output(preview_path)
            if can_export and self._is_output_path_within_root(output_path, base_dest):
                self._remove_output_file(output_path)
            return
        if not can_export:
            self.state.add_active_output(preview_path, "多元")
            return

        # Cached paths can outlive a change to the configured output directory.
        if not self._is_output_path_within_root(output_path, base_dest):
            return

        self.state.add_active_output(output_path, "多元")
        selected = self.state.get_output_selection().get(basename, False)
        if selected and basename not in self.state.get_blacklist():
            atomic_write(output_path, "\n".join(sorted(all_lines)))
        else:
            self._remove_output_file(output_path)

    @staticmethod
    def _remove_output_file(path):
        try:
            if not stat.S_ISREG(os.stat(path).st_mode):
                raise OSError("输出位置不是普通文件")
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
        except FileNotFoundError:
            pass

    def _get_all_source_files(self):
        paths = set()
        sources = self.state.get_source_files()
        for path, data in sources.items():
            if not data.get("enabled"): continue
            if is_excluded(path, self.state.get_excluded_paths()): continue
            if data.get("type") == "folder" and os.path.isdir(path):
                discovered, scan_failed = scan_markdown(path, self.state.get_excluded_paths())
                # Preserve known entries when a subtree is temporarily unreadable.
                if scan_failed:
                    discovered = {**data.get("files", {}), **discovered}
                data['files'] = discovered
                self.state.update_source_file(path, data)
            source_paths = data.get("files", {}).keys() if data.get("type") == "folder" else (path,)
            for source_path in source_paths:
                if is_excluded(source_path, self.state.get_excluded_paths()): continue
                paths.add(os.path.normcase(os.path.realpath(os.path.abspath(source_path))))
        return list(paths)

    def _get_enabled_event_paths(self, changed_paths):
        exclusions = self.state.get_excluded_paths()
        roots = [(normalize(path), kind) for path, kind in self.state.get_enabled_source_roots()]
        return {
            path for path in changed_paths
            if not is_excluded(path, exclusions)
            and any(path == root or (kind == 'folder' and _is_within_folder(path, root))
                    for root, kind in roots)
        }
