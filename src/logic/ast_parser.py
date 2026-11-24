import re
from collections import defaultdict


_BACKTICK_RUN = re.compile(r"`+")


def _safe_library_filename(library_name: str):
    """Return a safe output filename, or None for path-like/invalid labels."""
    library_name = library_name.strip()
    if not library_name or ".." in library_name:
        return None
    if any(char in library_name for char in '/\\<>:"|?*\0'):
        return None
    return f"#KV树-{library_name}.md"

def compile_rule(rule):
    pattern = re.compile(rule.get("match", ""))
    replacement = rule.get("replace", "")
    if replacement:
        # Compile the replacement template even if no input line matches.
        pattern.sub(replacement, "")
    return {"match": pattern, "replace": replacement}


class Node:
    """
    表示 AST 中的一个节点，对应于源文本中的一行。
    """
    def __init__(self, raw_line: str, indent: int, parent=None, code=False):
        self.raw_line = raw_line  # 原始行内容
        self.indent = indent      # 缩进级别
        self.parent = parent      # 父节点
        self.children = []        # 子节点列表
        self.tag = None           # 解析出的标签信息 (e.g., {'lib': '读书词库', 'mode': '父与子'})
        self.line_without_tag = raw_line
        self.content = raw_line.strip() if code else self._parse_content()

    def _parse_content(self):
        """从原始行中解析出标签和纯内容。"""
        tag_pattern = re.compile(r'#KV树-(?P<lib>[^-`]+)(?:-(?P<mode>父与子|不包含父))?$')
        line = self.raw_line.rstrip()
        ticks = list(_BACKTICK_RUN.finditer(line))
        spans = []
        i = 0
        while i < len(ticks):
            opening = ticks[i]
            i += 1
            for j in range(i, len(ticks)):
                if ticks[j].group() == opening.group():
                    spans.append((opening.start(), ticks[j].end()))
                    i = j + 1
                    break
        for start in re.finditer(r'#KV树-', line):
            if any(left <= start.start() < right for left, right in spans):
                continue
            match = tag_pattern.match(line, start.start())
            if match is None:
                continue
            gd = match.groupdict()
            library_filename = _safe_library_filename(gd['lib'])
            if library_filename is None:
                return self.raw_line.strip().lstrip('- ').strip()
            self.tag = {
                'lib': library_filename,
                'mode': gd['mode']
            }
            # 返回移除了标签和前导 `- ` 的内容
            self.line_without_tag = line[:match.start()].rstrip()
            return self.line_without_tag.lstrip('- ').strip()
        # 如果没有标签，同样移除前导 `- `
        return self.raw_line.strip().lstrip('- ').strip()

    def add_child(self, node):
        """添加一个子节点。"""
        self.children.append(node)
        node.parent = self

    def __repr__(self):
        return f"Node(indent={self.indent}, content='{self.content}', tag={self.tag})"


class AstParser:
    """
    使用 AST 方法解析文本，以更健壮地处理层级关系。
    """
    def get_indent(self, s: str) -> int:
        """计算字符串的缩进量。"""
        return len(s) - len(s.lstrip(' \t'))

    def _build_ast(self, lines: list[str]) -> Node:
        """
        从文本行构建一个 AST。
        """
        root = Node("root", -1)
        ancestors = [root]
        fence = None

        for line in lines:
            if not line.strip():
                continue # 暂时忽略空行，后续可以作为节点处理

            indent = self.get_indent(line)
            
            while ancestors[-1].indent >= indent:
                ancestors.pop()
            parent_node = ancestors[-1]

            marker = re.match(r'^\s*(?:-\s+)?(?P<run>`{3,}|~{3,})(?P<tail>.*)$', line)
            code = fence is not None
            if fence is not None:
                if (marker and marker['run'][0] == fence[0]
                        and len(marker['run']) >= fence[1]
                        and not marker['tail'].strip()):
                    fence = None
            elif marker and not (marker['run'][0] == '`' and '`' in marker['tail']):
                fence = (marker['run'][0], len(marker['run']))
                code = True
            new_node = Node(line, indent, parent=parent_node, code=code)
            parent_node.add_child(new_node)
            
            ancestors.append(new_node)

        return root

    def parse(self, text: str, rules: dict = None) -> tuple[dict, list]:
        """
        解析文本并返回结果。
        这是将替换旧解析器的主入口点。
        """
        # 1. 清理和预处理文本行
        # 0. 标准化输入：将所有 Tab 替换为 4 个空格
        text = text.replace('\t', '    ')
        
        # 1. 清理和预处理文本行
        lines = text.split('\n')
        processed_lines = self._preprocess_lines(lines, rules)

        # 2. 构建 AST
        root = self._build_ast(processed_lines)

        # 3. 从 AST 中提取数据
        kv_trees = self._extract_data(root)

        # 4. 格式化最终输出
        final_results = {lib: list(dict.fromkeys(entries)) for lib, entries in kv_trees.items()}
        return final_results, [] # 暂时不处理冲突

    def _preprocess_lines(self, lines: list[str], rules: dict) -> list[str]:
        """根据规则列表清理和替换内容。"""
        if not rules or not isinstance(rules, dict):
            return lines
            
        line_rules = rules.get("line_rules", [])
        content_rules = rules.get("content_rules", [])

        compiled_line = [compile_rule(rule) for rule in line_rules]
        compiled_content = [compile_rule(rule) for rule in content_rules]

        processed_lines = []
        for line in lines:
            drop_line = False
            # 1. 优先执行“排除行”规则
            for cr in compiled_line:
                if cr["match"].search(line):
                    repl = cr["replace"]
                    if not repl:
                        drop_line = True
                        break
                    else:
                        line = cr["match"].sub(repl, line)
            
            if drop_line:
                continue
                
            # 2. 执行“排除内容”规则
            for cr in compiled_content:
                line = cr["match"].sub(cr["replace"], line)
                
            processed_lines.append(line)

        return processed_lines

    def _extract_data(self, root: Node) -> defaultdict:
        kv_trees = defaultdict(list)
        processed_nodes = set()

        all_nodes = []
        def get_all_nodes(node):
            all_nodes.append(node)
            for child in node.children:
                get_all_nodes(child)
        get_all_nodes(root)

        # Phase 1: Process block-level tags ('父与子', '不包含父')
        for node in all_nodes:
            if node in processed_nodes or not node.tag or not node.tag.get('mode'):
                continue
            
            lib = node.tag['lib']
            mode = node.tag['mode']
            
            processed_nodes.add(node) # Mark parent as processed

            if mode == '父与子':
                self._append_word_entry(kv_trees[lib], node)
            self._render_block_children(node, lib, kv_trees, processed_nodes)

        # Phase 2: Process all remaining single-line tags
        for node in all_nodes:
            # Bug fix: If a node has a tag, AND it is a pure single-line tag (no 'mode' like 父与子),
            # it MUST be rendered as a single-line output EVEN IF it was already processed as part of a block.
            if not node.tag or node.tag.get('mode'):
                continue
            
            lib = node.tag['lib']
            self._append_word_entry(kv_trees[lib], node)
            processed_nodes.add(node)

        return kv_trees

    @staticmethod
    def _append_word_entry(entries, node):
        # QuickKV starts a new word entry only on a top-level "- " line.
        line = node.line_without_tag.strip()
        if line == "-":
            return
        if line.startswith("- "):
            line = line[2:].strip()
        if line:
            entries.append(f"- {line}")

    def _render_block_children(self, parent_node: Node, lib: str, kv_trees: defaultdict, processed_nodes: set):
        """Export each eligible descendant as a separate word entry."""
        for node in parent_node.children:
            if node in processed_nodes or (node.tag and node.tag.get('mode')):
                continue

            processed_nodes.add(node)
            self._append_word_entry(kv_trees[lib], node)

            if not node.tag:
                self._render_block_children(node, lib, kv_trees, processed_nodes)
