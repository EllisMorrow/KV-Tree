import os


DEFAULT_EXCLUDED_PATHS = ['.stversions', '.tine-trash', '.trash', '.recycle', 'bak', '.stfolder', '.obsidian']


def _is_link(path):
    # Junctions are reparse points on Windows, including Python 3.10.
    try:
        info = os.lstat(path)
        return os.path.islink(path) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)
    except OSError:
        return True


def normalize(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_excluded(path, rules):
    candidate = normalize(path)
    parts = candidate.replace('\\', '/').split('/')
    for rule in rules:
        rule = rule.strip().replace('\\', '/').rstrip('/')
        if not rule:
            continue
        if '/' not in rule and not os.path.isabs(rule):
            if os.path.normcase(rule) in parts:
                return True
        elif os.path.isabs(rule):
            target = normalize(rule)
            try:
                if os.path.commonpath((candidate, target)) == target:
                    return True
            except ValueError:
                pass
        elif ('/' + os.path.normcase(rule) + '/') in ('/' + candidate.replace('\\', '/') + '/'):
            return True
    return False


def scan_markdown(root, rules):
    files, errors = {}, []
    if is_excluded(root, rules):
        return files, errors
    for directory, children, names in os.walk(root, onerror=errors.append, followlinks=False):
        children[:] = [name for name in children
                       if not is_excluded(os.path.join(directory, name), rules)
                       and not _is_link(os.path.join(directory, name))]
        for name in names:
            path = os.path.join(directory, name)
            if name.lower().endswith('.md') and not is_excluded(path, rules):
                try:
                    files[path] = os.path.getmtime(path)
                except OSError as error:
                    errors.append(error)
    return files, errors
