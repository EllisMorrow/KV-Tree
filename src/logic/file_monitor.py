import os

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from src.logic.path_filter import is_excluded


def _normalize_path(path):
    """Return a stable path representation for source-scope comparisons."""
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.normpath(path))))


def _is_within_folder(path, folder):
    """Whether *path* is inside *folder*, without prefix-based false matches."""
    try:
        return os.path.commonpath((path, folder)) == folder
    except ValueError:
        # Different drives cannot share a common parent on Windows.
        return False


class AppEventHandler(FileSystemEventHandler):
    """Filter filesystem events to enabled sources before queueing work."""

    def __init__(self, task_dispatcher, app_state):
        super().__init__()
        self.dispatcher = task_dispatcher
        self.state = app_state

    def process_event(self, event_type, path):
        path = _normalize_path(path)
        if not path.lower().endswith(".md") or is_excluded(path, self.state.get_excluded_paths()):
            return

        folder_roots = []
        matched_file = False
        for source, kind in self.state.get_enabled_source_roots():
            root = _normalize_path(source)
            if kind == "folder":
                if _is_within_folder(path, root):
                    folder_roots.append(source)
            elif root == path:
                matched_file = True
        if not folder_roots and not matched_file:
            return

        if folder_roots:
            if event_type == "deleted":
                self.state.update_folder_members(folder_roots, path, None)
            else:
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    pass  # The worker will check the file's final state.
                else:
                    self.state.update_folder_members(folder_roots, path, mtime)
        self.dispatcher.put_task(("process_file", event_type, path))

    def on_modified(self, event):
        if not event.is_directory:
            self.process_event("modified", event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self.process_event("created", event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self.process_event("deleted", event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            # Check each side independently: a file can move into or out of a
            # watched source, and only the in-scope side should be queued.
            self.process_event("deleted", event.src_path)
            self.process_event("created", event.dest_path)


class FileMonitor:
    """Manage watchdog observers for enabled source paths."""

    def __init__(self, task_dispatcher, app_state, ui_callbacks):
        self.dispatcher = task_dispatcher
        self.state = app_state
        self.ui_cb = ui_callbacks
        self.observer = None
        self.event_handler = AppEventHandler(self.dispatcher, self.state)

    def start(self):
        """Start the smallest set of watches needed for enabled sources."""
        if self.observer and self.observer.is_alive():
            self.stop()

        self.observer = Observer()
        sources = self.state.get_source_files()
        folder_roots = []
        file_parents = []

        for source_path, source_data in sources.items():
            if not source_data.get("enabled"):
                continue

            normalized_source = _normalize_path(source_path)
            if source_data.get("type") == "folder":
                if os.path.isdir(normalized_source):
                    folder_roots.append(normalized_source)
            else:
                parent = os.path.dirname(normalized_source)
                if os.path.isdir(parent):
                    file_parents.append(parent)

        # A recursive parent watch already covers all nested folder watches.
        recursive_roots = []
        for folder in sorted(set(folder_roots), key=lambda item: (len(item), item)):
            if not any(_is_within_folder(folder, root) for root in recursive_roots):
                recursive_roots.append(folder)

        non_recursive_roots = []
        for parent in sorted(set(file_parents)):
            if not any(_is_within_folder(parent, root) for root in recursive_roots):
                non_recursive_roots.append(parent)

        for watch_path in recursive_roots:
            self.observer.schedule(self.event_handler, watch_path, recursive=True)
        for watch_path in non_recursive_roots:
            self.observer.schedule(self.event_handler, watch_path, recursive=False)

        watch_count = len(recursive_roots) + len(non_recursive_roots)
        if watch_count:
            self.observer.start()
            self.ui_cb['set_status'](f"监控已开启，正在监视 {watch_count} 个位置。")
        else:
            self.ui_cb['set_status']("监控开启，但无启用的源可供监视。")

    def stop(self):
        """Stop monitoring."""
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join()
            self.observer = None
            self.ui_cb['set_status']("监控已关闭。")
