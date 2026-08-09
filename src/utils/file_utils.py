import json
import os
import stat
import tempfile


def _atomic_write(filepath, write_content, encoding="utf-8", suffix=""):
    """Write through a same-directory temporary file, then atomically replace."""
    directory = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=directory,
        prefix=".tmp_",
        suffix=suffix,
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            write_content(f)
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def atomic_write_json(filepath, data, encoding="utf-8", ensure_ascii=False, indent=4):
    """Atomically serialize JSON without changing the target file's permissions."""
    _atomic_write(
        filepath,
        lambda f: json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent),
        encoding=encoding,
        suffix=".json",
    )


def atomic_write(filepath, content, encoding="utf-8"):
    """
    Safely writes content to filepath using a temporary file and atomic replace.
    Prevents truncation and 0-byte files if process is interrupted.
    """
    # Markdown outputs intentionally become read-only after a successful write.
    try:
        # If target file exists and is read-only, we must change its permissions first
        if os.path.exists(filepath):
            try:
                os.chmod(filepath, stat.S_IWRITE)
            except Exception:
                pass # Best effort
        _atomic_write(
            filepath,
            lambda f: f.write(content),
            encoding=encoding,
            suffix=".md",
        )
        
        # Set file to read-only as per application logic
        try:
            os.chmod(filepath, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        except Exception:
            pass # Best effort
    except Exception:
        raise
