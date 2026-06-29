#!/usr/bin/env python3
r"""
MDViewer — 桌面 Markdown 文件浏览器（独立窗口，嵌入式浏览器）

用法：
    python mdviewer.py                        # 浏览当前目录
    python mdviewer.py D:/docs                # 浏览指定目录
    python mdviewer.py D:/docs/README.md      # 打开指定文件

特性：
    - 原生窗口 + 系统 WebView（PyWebView）
    - 左侧文件树 + 右侧内容渲染
    - 📂 打开文件对话框
    - 🖱️ 拖放 .md 文件到窗口直接打开
    - 内置 Markdown → HTML 渲染器（零外部依赖）
    - 可选：安装 markdown 库获得代码高亮
    - 支持打包为独立 exe
"""

import sys
import os
import re
import json
import socket
import threading
import time
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import urllib.parse

HOST = "127.0.0.1"
PORT = 18765
ROOT_DIR = Path.cwd()


# ═══════════════════════════════════════════════════════════
#  内置 Markdown → HTML 渲染器
# ═══════════════════════════════════════════════════════════

def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Precompiled regex for _inline_format (called per line, many times)
_RE_IMG = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
_RE_LINK = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')
_RE_BOLD1 = re.compile(r'\*\*(.+?)\*\*')
_RE_BOLD2 = re.compile(r'__(.+?)__')
_RE_ITALIC1 = re.compile(r'\*(.+?)\*')
_RE_ITALIC2 = re.compile(r'_(.+?)_')
_RE_CODE = re.compile(r'`([^`]+)`')
_RE_DEL = re.compile(r'~~(.+?)~~')


def _inline_format(text: str) -> str:
    t = _esc(text)
    t = _RE_IMG.sub(r'<img src="\2" alt="\1">', t)
    t = _RE_LINK.sub(r'<a href="\2">\1</a>', t)
    t = _RE_BOLD1.sub(r'<strong>\1</strong>', t)
    t = _RE_BOLD2.sub(r'<strong>\1</strong>', t)
    t = _RE_ITALIC1.sub(r'<em>\1</em>', t)
    t = _RE_ITALIC2.sub(r'<em>\1</em>', t)
    t = _RE_CODE.sub(r'<code>\1</code>', t)
    t = _RE_DEL.sub(r'<del>\1</del>', t)
    return t


_RE_SEPARATOR = re.compile(r'^[-:]+$')


def _render_table(rows: list) -> str:
    if len(rows) < 2:
        return '<p>' + ' | '.join(rows) + '</p>'
    data_rows = []
    for r in rows:
        cells = [c.strip() for c in r.strip().split('|')]
        cells = [c for c in cells if c]
        if all(_RE_SEPARATOR.match(c) for c in cells):
            continue
        data_rows.append(cells)
    if not data_rows:
        return ''
    html = ['<table><thead><tr>']
    for cell in data_rows[0]:
        html.append(f'<th>{_inline_format(cell)}</th>')
    html.append('</tr></thead>')
    if len(data_rows) > 1:
        html.append('<tbody>')
        for row in data_rows[1:]:
            html.append('<tr>')
            for cell in row:
                html.append(f'<td>{_inline_format(cell)}</td>')
            html.append('</tr>')
        html.append('</tbody>')
    html.append('</table>')
    return '\n'.join(html)


# Precompiled regex for md_to_html
_RE_HEADING = re.compile(r'^(#{1,6})\s+(.+)$')
_RE_HR = re.compile(r'^[-*_]{3,}\s*$')
_RE_UL = re.compile(r'^(\s*)[-*+]\s+(.+)$')
_RE_OL = re.compile(r'^(\s*)\d+\.\s+(.+)$')
_RE_HEADING_ID = re.compile(r'[^a-z0-9\u4e00-\u9fff]+')
_RE_TASK_UNCHECKED = re.compile(r'<li>\[ \] (.*?)</li>')
_RE_TASK_CHECKED = re.compile(r'<li>\[x\] (.*?)</li>', re.IGNORECASE)


def md_to_html(text: str) -> str:
    lines = text.split('\n')
    out = []
    in_code_block = False
    in_table = False
    in_list = False
    list_tag = ''
    table_rows = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.strip().startswith('```'):
            if not in_code_block:
                lang = line.strip()[3:].strip()
                cls = f' class="language-{_esc(lang)}"' if lang else ''
                out.append(f'<pre><code{cls}>')
                in_code_block = True
            else:
                out.append('</code></pre>')
                in_code_block = False
            i += 1
            continue

        if in_code_block:
            out.append(_esc(line))
            i += 1
            continue

        if '|' in line and line.strip().startswith('|'):
            if not in_table:
                in_table = True
                table_rows = []
            table_rows.append(line)
            i += 1
            continue
        elif in_table:
            out.append(_render_table(table_rows))
            in_table = False
            table_rows = []

        m = _RE_HEADING.match(line)
        if m:
            level = len(m.group(1))
            id_attr = _RE_HEADING_ID.sub('-', m.group(2).lower()).strip('-')
            out.append(f'<h{level} id="{id_attr}">{m.group(2)}</h{level}>')
            i += 1
            continue

        if _RE_HR.match(line.strip()):
            out.append('<hr>')
            i += 1
            continue

        if line.startswith('> '):
            out.append(f'<blockquote><p>{_inline_format(line[2:])}</p></blockquote>')
            i += 1
            continue

        m = _RE_UL.match(line)
        if m:
            if not in_list or list_tag != 'ul':
                if in_list:
                    out.append(f'</{list_tag}>')
                out.append('<ul>')
                list_tag = 'ul'
                in_list = True
            out.append(f'<li>{_inline_format(m.group(2))}</li>')
            i += 1
            continue

        m = _RE_OL.match(line)
        if m:
            if not in_list or list_tag != 'ol':
                if in_list:
                    out.append(f'</{list_tag}>')
                out.append('<ol>')
                list_tag = 'ol'
                in_list = True
            out.append(f'<li>{_inline_format(m.group(2))}</li>')
            i += 1
            continue

        if in_list:
            out.append(f'</{list_tag}>')
            in_list = False
            list_tag = ''

        if not line.strip():
            out.append('')
            i += 1
            continue

        out.append(f'<p>{_inline_format(line)}</p>')
        i += 1

    if in_code_block:
        out.append('</code></pre>')
    if in_table:
        out.append(_render_table(table_rows))
    if in_list:
        out.append(f'</{list_tag}>')

    html = '\n'.join(out)
    html = _RE_TASK_UNCHECKED.sub(r'<li><input type="checkbox" disabled> \1</li>', html)
    html = _RE_TASK_CHECKED.sub(r'<li><input type="checkbox" checked disabled> \1</li>', html)
    return html


def _detect_encoding(raw: bytes) -> str:
    if not raw:
        return "utf-8"
    # 1) BOM detection (fast)
    if raw.startswith(b'\xef\xbb\xbf'):
        return "utf-8-sig"
    if raw.startswith(b'\xff\xfe'):
        return "utf-16-le"
    if raw.startswith(b'\xfe\xff'):
        return "utf-16-be"
    # 2) Try UTF-8
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    # 3) Try chardet
    try:
        import chardet
        result = chardet.detect(raw)
        if result and result["encoding"] and result["confidence"] > 0.5:
            enc = result["encoding"].lower()
            if enc in ("gb2312", "gbk", "gb18030"):
                return "gbk"
            if enc == "ascii":
                return "utf-8"
            return enc
    except ImportError:
        pass
    # 4) Try common encodings
    for enc in ("gbk", "gb18030", "gb2312", "big5", "shift_jis", "euc-kr"):
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"


def render_file(path: Path) -> str:
    raw = path.read_bytes()
    encoding = _detect_encoding(raw)
    text = raw.decode(encoding, errors="replace")

    if path.suffix.lower() == ".md":
        try:
            import markdown as md_lib
            return md_lib.markdown(text, extensions=["fenced_code", "tables", "codehilite", "toc", "sane_lists"],
                                   extension_configs={"codehilite": {"guess_lang": True}})
        except ImportError:
            return md_to_html(text)
    else:
        # 纯文本文件
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # 尝试根据扩展名推测语言用于代码高亮
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".html": "html", ".css": "css", ".json": "json",
            ".xml": "xml", ".yaml": "yaml", ".yml": "yaml",
            ".sh": "bash", ".bat": "batch", ".ps1": "powershell",
            ".sql": "sql", ".java": "java", ".cpp": "cpp",
            ".c": "c", ".h": "c", ".rs": "rust", ".go": "go",
            ".toml": "toml", ".ini": "ini", ".cfg": "ini",
            ".csv": "csv", ".log": "log", ".txt": "text",
        }
        lang = ext_map.get(path.suffix.lower(), "")
        lang_attr = f' class="language-{lang}"' if lang else ""
        return f'<pre><code{lang_attr}>{escaped}</code></pre>'


def _format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def get_file_size(path: Path) -> str:
    try:
        return _format_size(path.stat().st_size)
    except OSError:
        return ""


def find_drives() -> list:
    """检测本地磁盘盘符列表"""
    drives = []
    # WSL: /mnt/c, /mnt/d, ...
    mnt = Path("/mnt")
    if mnt.exists():
        for d in sorted(mnt.iterdir()):
            if d.is_dir() and len(d.name) == 1:
                drives.append({"name": d.name.upper() + ":", "path": str(d)})
    if drives:
        return drives
    # Native Windows: GetLogicalDrives (bitmask, no disk I/O)
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                letter = chr(65 + i)
                drives.append({"name": letter + ":", "path": letter + ":\\"})
        if drives:
            return drives
    except Exception:
        pass
    # Linux/Mac: 根目录
    return [{"name": "/ (根目录)", "path": "/"}]


def _is_text_file(name: str) -> bool:
    """判断是否为文本文件（可浏览的文件类型）"""
    ext = Path(name).suffix.lower()
    # 无扩展名视为文本（如 Makefile, LICENSE, Dockerfile）
    if not ext:
        return True
    text_exts = {
        ".md", ".txt", ".log", ".csv",
        ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss", ".less",
        ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
        ".sh", ".bash", ".zsh", ".bat", ".ps1", ".cmd",
        ".sql", ".java", ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".rb", ".php",
        ".swift", ".kt", ".scala", ".r", ".pl", ".lua", ".vim",
        ".dockerfile", ".gitignore", ".makefile",
    }
    return ext in text_exts


def build_tree(dir_path: Path) -> list:
    items = []
    try:
        with os.scandir(dir_path) as scan:
            entries = sorted(scan, key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return items
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            items.append({"name": entry.name, "type": "dir"})
        elif entry.is_file() and _is_text_file(entry.name):
            try:
                sz = entry.stat().st_size
            except OSError:
                sz = 0
            items.append({
                "name": entry.name, "type": "file",
                "size": sz, "size_human": _format_size(sz),
                "is_md": Path(entry.name).suffix.lower() == ".md",
            })
    return items


# ═══════════════════════════════════════════════════════════
#  PyWebView JS→Python API 桥接
# ═══════════════════════════════════════════════════════════

class MdApi:
    """暴露给 JavaScript 的 API。通过 window.pywebview.api 调用。"""

    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    def open_file_dialog(self) -> dict:
        """打开原生文件选择对话框，返回选中文件的渲染结果"""
        if not self._window:
            return {"error": "窗口未初始化"}
        try:
            import webview
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                directory=str(ROOT_DIR),
                file_types=('Markdown 文件 (*.md)',),
            )
        except Exception as e:
            return {"error": str(e)}

        if not result or not result[0]:
            return {"cancelled": True}

        filepath = Path(result[0])
        return self._open_and_render(filepath)

    def open_external_file(self, filepath: str) -> dict:
        """打开指定路径的文件（拖放或命令行传入）"""
        p = Path(filepath)
        if not p.is_absolute():
            p = (ROOT_DIR / filepath).resolve()
        return self._open_and_render(p)

    def _open_and_render(self, filepath: Path) -> dict:
        global ROOT_DIR
        if not filepath.exists():
            return {"error": f"文件不存在: {filepath}"}
        if not filepath.is_file():
            return {"error": "不是文件"}

        try:
            html = render_file(filepath)
        except Exception as e:
            return {"error": f"读取失败: {e}"}

        # 自动切换到文件所在目录
        new_root = str(filepath.parent.resolve())
        old_root = str(ROOT_DIR.resolve())

        return {
            "path": str(filepath),
            "name": filepath.name,
            "html": html,
            "size": get_file_size(filepath),
            "dir": str(filepath.parent),
            "root_changed": new_root != old_root,
            "new_root": new_root,
        }

    def get_tree(self, dir_path: str) -> dict:
        """获取目录树"""
        p = Path(dir_path) if dir_path else ROOT_DIR
        if not p.is_absolute():
            p = ROOT_DIR / dir_path
        p = p.resolve()
        children = build_tree(p)
        return {
            "root": str(ROOT_DIR),
            "dir": dir_path or ".",
            "children": children,
        }

    def change_root(self, new_root: str) -> dict:
        """切换根目录"""
        global ROOT_DIR
        p = Path(new_root).resolve()
        if p.is_dir():
            ROOT_DIR = p
            return self.get_tree("")
        return {"error": "无效目录"}

    def get_drives(self) -> dict:
        """获取盘符列表"""
        return {"drives": find_drives()}


# ═══════════════════════════════════════════════════════════
#  HTML 单页应用（文件树 + 内容区 + 拖放）
# ═══════════════════════════════════════════════════════════

APP_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MDViewer</title>
<style>
:root, [data-theme="light"] {
    --bg: #ffffff; --bg2: #f6f8fa; --border: #d0d7de;
    --text: #1f2328; --text2: #656d76; --link: #0969da;
    --code-bg: #f6f8fa; --sidebar-w: 260px;
    --drop-bg: #d4e2ff; --hover-bg: #e8eaed;
    --active-bg: #d4e2ff; --scrollbar-thumb: #c1c1c1;
    --btn-bg: #ffffff; --btn-hover-bg: #f6f8fa;
    --drive-bg: #f6f8fa; --drive-hover-bg: #d4e2ff;
}
[data-theme="dark"] {
    --bg: #0d1117; --bg2: #161b22; --border: #30363d;
    --text: #e6edf3; --text2: #8b949e; --link: #58a6ff;
    --code-bg: #161b22; --sidebar-w: 260px;
    --drop-bg: #1f2a3a; --hover-bg: #1c2533;
    --active-bg: #1f2a3a; --scrollbar-thumb: #484f58;
    --btn-bg: #21262d; --btn-hover-bg: #30363d;
    --drive-bg: #21262d; --drive-hover-bg: #1f2a3a;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; overflow: hidden; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", sans-serif;
    font-size: 15px; color: var(--text); display: flex;
}

/* ── 侧边栏 ── */
#sidebar {
    width: var(--sidebar-w); min-width: var(--sidebar-w);
    height: 100vh; background: var(--bg2);
    border-right: 1px solid var(--border);
    display: flex; flex-direction: column; overflow: hidden;
}
#sidebar-header {
    padding: 12px 14px; border-bottom: 1px solid var(--border);
    font-weight: 600; font-size: 14px;
    display: flex; align-items: center; gap: 6px;
    background: var(--bg); user-select: none; flex-shrink: 0;
}
#sidebar-header .root-path {
    flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-size: 12px; color: var(--text2); font-weight: 400;
}

/* 文件树区域 */
#tree-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-height: 80px; }
#file-tree { flex: 1; overflow-y: auto; padding: 4px 0; font-size: 13px; }
#file-tree::-webkit-scrollbar { width: 6px; }
#file-tree::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 3px; }

/* 分隔条 */
#split-handle {
    height: 4px; background: var(--border); cursor: row-resize;
    flex-shrink: 0; transition: background .15s;
}
#split-handle:hover { background: var(--link); }

/* 大纲区域 */
#outline-panel {
    height: 200px; display: flex; flex-direction: column;
    overflow: hidden; flex-shrink: 0; min-height: 40px;
}
#outline-header {
    padding: 8px 14px; font-size: 12px; font-weight: 600;
    color: var(--text2); border-top: 1px solid var(--border);
    background: var(--bg); flex-shrink: 0; user-select: none;
    display: flex; align-items: center; gap: 4px;
}
#outline-header .count { font-weight: 400; opacity: 0.7; }
#outline-list { flex: 1; overflow-y: auto; padding: 4px 0; font-size: 12px; }
#outline-list::-webkit-scrollbar { width: 5px; }
#outline-list::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 2px; }

.outline-item {
    display: block; padding: 2px 14px; cursor: pointer;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    color: var(--text2); text-decoration: none; user-select: none;
    transition: background .1s; border-left: 2px solid transparent;
}
.outline-item:hover { background: var(--hover-bg); color: var(--text); }
.outline-item.active { background: var(--active-bg); color: var(--link); border-left-color: var(--link); }
.outline-item.lv1 { padding-left: 14px; font-weight: 600; }
.outline-item.lv2 { padding-left: 24px; }
.outline-item.lv3 { padding-left: 34px; }
.outline-item.lv4 { padding-left: 44px; }
.outline-empty { padding: 12px 14px; color: var(--text2); font-size: 12px; text-align: center; }

.tree-item {
    display: flex; align-items: center; gap: 4px;
    padding: 3px 14px; cursor: pointer; user-select: none;
    white-space: nowrap; transition: background .1s;
}
.tree-item:hover { background: var(--hover-bg); }
.tree-item.active { background: var(--active-bg); font-weight: 500; }
.tree-item.up-dir { color: var(--link); font-weight: 500; }
.tree-item .icon { font-size: 14px; width: 18px; text-align: center; flex-shrink: 0; }
.tree-item .name { overflow: hidden; text-overflow: ellipsis; }
.tree-item .size { color: var(--text2); font-size: 11px; margin-left: auto; flex-shrink: 0; }
.tree-item.folder { font-weight: 500; }
.tree-item.folder .icon { font-size: 14px; }
/* 旧的展开/折叠和嵌套缩进已移除，现在是单层导航 */
.tree-children { display: none; }
.empty-tree { padding: 20px 14px; color: var(--text2); font-size: 13px; text-align: center; }

/* 盘符列表 */
#drives-bar {
    display: flex; flex-wrap: wrap; gap: 3px;
    padding: 5px 8px; border-bottom: 1px solid var(--border);
    background: var(--bg); flex-shrink: 0;
}
.drive-item {
    padding: 3px 10px; border-radius: 4px; cursor: pointer;
    font-size: 12px; font-weight: 500; color: var(--text2);
    background: var(--drive-bg); border: 1px solid var(--border);
    transition: all .15s; user-select: none;
}
.drive-item:hover { background: var(--drive-hover-bg); color: var(--link); border-color: var(--link); }
.drive-item.active { background: var(--link); color: #fff; border-color: var(--link); }

/* ── 主内容区 ── */
#main {
    flex: 1; height: 100vh; display: flex; flex-direction: column;
    overflow: hidden; position: relative;
}
#toolbar {
    padding: 8px 16px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 8px;
    background: var(--bg); flex-shrink: 0;
}
#toolbar .path-breadcrumb {
    flex: 1; font-size: 13px; color: var(--text2);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
#toolbar button {
    padding: 4px 10px; border: 1px solid var(--border);
    background: var(--btn-bg); border-radius: 4px; cursor: pointer;
    font-size: 12px; color: var(--text);
}
#toolbar button:hover { background: var(--btn-hover-bg); }
#content {
    flex: 1; overflow-y: auto; padding: 32px 40px;
    width: 100%; line-height: 1.7; background: var(--bg);
}
#content::-webkit-scrollbar { width: 8px; }
#content::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 4px; }

/* ── 拖放提示层 ── */
#drop-overlay {
    display: none; position: absolute; inset: 0;
    background: var(--drop-bg); z-index: 100;
    align-items: center; justify-content: center;
    border: 3px dashed var(--link); margin: 8px; border-radius: 12px;
    pointer-events: none;
}
#drop-overlay.show { display: flex; }
#drop-overlay .msg { font-size: 24px; color: var(--link); font-weight: 600; }

/* ── Markdown 排版 ── */
#content h1, #content h2, #content h3, #content h4, #content h5, #content h6 {
    margin: 24px 0 16px; font-weight: 600; line-height: 1.25;
}
#content h1 { font-size: 2em; border-bottom: 1px solid var(--border); padding-bottom: .3em; }
#content h2 { font-size: 1.5em; border-bottom: 1px solid var(--border); padding-bottom: .3em; }
#content h3 { font-size: 1.25em; }
#content p { margin: 0 0 16px; }
#content a { color: var(--link); text-decoration: none; }
#content a:hover { text-decoration: underline; }
#content ul, #content ol { padding-left: 2em; margin-bottom: 16px; }
#content li { margin-bottom: 4px; }
#content blockquote {
    margin: 0 0 16px; padding: 0 1em; color: var(--text2);
    border-left: .25em solid var(--border);
}
#content code {
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    font-size: 85%; background: var(--code-bg);
    padding: .2em .4em; border-radius: 6px;
}
#content pre {
    margin: 0 0 16px; padding: 16px; background: var(--code-bg);
    border-radius: 6px; overflow-x: auto; line-height: 1.45;
}
#content pre code { padding: 0; background: none; font-size: 100%; }
/* ── 代码语法高亮（Pygments codehilite 输出）── */
.highlight .k { color: #d73a49; font-weight: 600; }   /* keyword */
.highlight .nf { color: #6f42c1; }                     /* function */
.highlight .nc { color: #6f42c1; }                     /* class */
.highlight .n { color: #24292e; }                      /* name */
.highlight .s, .highlight .s1, .highlight .s2 { color: #032f62; } /* string */
.highlight .c, .highlight .c1, .highlight .cm { color: #6a737d; font-style: italic; } /* comment */
.highlight .p { color: #24292e; }                      /* punctuation */
.highlight .o { color: #d73a49; }                      /* operator */
.highlight .m, .highlight .mi, .highlight .mf { color: #005cc5; } /* number */
.highlight .nb { color: #005cc5; }                     /* builtin */
.highlight .kn { color: #d73a49; font-weight: 600; }   /* import */
.highlight .nn { color: #24292e; }                     /* namespace */
.highlight .bp { color: #005cc5; }                     /* self */
.highlight .nd { color: #6f42c1; }                     /* decorator */
.highlight .err { color: #cb2431; background: #ffeef0; }
.highlight .gh { color: #6a737d; }
/* 暗色主题 */
[data-theme="dark"] .highlight .k { color: #ff7b72; }
[data-theme="dark"] .highlight .nf { color: #d2a8ff; }
[data-theme="dark"] .highlight .nc { color: #d2a8ff; }
[data-theme="dark"] .highlight .n { color: #e6edf3; }
[data-theme="dark"] .highlight .s, [data-theme="dark"] .highlight .s1, [data-theme="dark"] .highlight .s2 { color: #a5d6ff; }
[data-theme="dark"] .highlight .c, [data-theme="dark"] .highlight .c1, [data-theme="dark"] .highlight .cm { color: #8b949e; }
[data-theme="dark"] .highlight .p { color: #e6edf3; }
[data-theme="dark"] .highlight .o { color: #ff7b72; }
[data-theme="dark"] .highlight .m, [data-theme="dark"] .highlight .mi, [data-theme="dark"] .highlight .mf { color: #79c0ff; }
[data-theme="dark"] .highlight .nb { color: #79c0ff; }
[data-theme="dark"] .highlight .kn { color: #ff7b72; }
[data-theme="dark"] .highlight .nn { color: #e6edf3; }
[data-theme="dark"] .highlight .bp { color: #79c0ff; }
[data-theme="dark"] .highlight .nd { color: #d2a8ff; }
#content table {
    border-collapse: collapse; width: 100%; margin-bottom: 16px;
    display: block; overflow-x: auto;
}
#content th, #content td { border: 1px solid var(--border); padding: 6px 13px; }
#content th { background: var(--bg2); font-weight: 600; }
#content tr:nth-child(even) { background: var(--bg2); }
#content hr { border: 0; border-top: 1px solid var(--border); margin: 24px 0; }
#content img { max-width: 100%; height: auto; }
#content input[type="checkbox"] { margin-right: .5em; }

.welcome {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; height: 100%; color: var(--text2);
    text-align: center; gap: 8px;
}
.welcome .icon { font-size: 48px; }
.welcome h2 { border: none; font-size: 1.2em; color: var(--text); }
.welcome p { font-size: 14px; max-width: 400px; }

#statusbar {
    padding: 4px 16px; border-top: 1px solid var(--border);
    font-size: 11px; color: var(--text2); display: flex; gap: 12px;
    flex-shrink: 0; background: var(--bg);
}
</style>
</head>
<body>

<div id="sidebar">
    <div id="drives-bar"></div>
    <div id="sidebar-header">
        📁 <span class="root-path" id="root-label">加载中...</span>
    </div>
    <div id="tree-panel">
        <div id="file-tree"><div class="empty-tree">📭 加载中...</div></div>
    </div>
    <div id="split-handle" title="拖动调整大纲高度"></div>
    <div id="outline-panel">
        <div id="outline-header">📑 大纲 <span class="count"></span></div>
        <div id="outline-list"><div class="outline-empty">打开文件后显示</div></div>
    </div>
</div>

<div id="main">
    <div id="toolbar">
        <span class="path-breadcrumb" id="breadcrumb">选择文件开始浏览</span>
        <button onclick="toggleTheme()" title="切换日间/夜间模式" id="theme-btn">🌙</button>
        <button onclick="btnOpenFile()" title="打开文件">📂 打开</button>
        <button onclick="location.reload()" title="刷新">🔄 刷新</button>
    </div>
    <input type="file" id="file-input" style="display:none" onchange="onFileInputChange(event)">
    <div id="content">
        <div class="welcome">
            <div class="icon">📄</div>
            <h2>MDViewer</h2>
            <p>左侧文件树选择文件 · 拖放 .md 文件 · 点击 📂 打开 · Ctrl+O 打开 · Ctrl+Q 退出</p>
            <p style="font-size:12px;opacity:0.7">支持 GitHub 风格 Markdown 渲染</p>
        </div>
    </div>
    <div id="drop-overlay"><span class="msg">📥 放开以打开文件</span></div>
    <div id="statusbar">
        <span id="status-file">未打开文件</span>
        <span id="status-lines" style="margin-left:auto"></span>
    </div>
</div>

<script>
let currentFilePath = null;
let currentTreeDir = '';
let outlineObserver = null;

// ── 检测 API 模式 ──
const hasNativeApi = !!(window.pywebview && window.pywebview.api);

async function api(path, options) {
    if (hasNativeApi) {
        return await window.pywebview.api[path](...(options || []));
    } else {
        let url = '/api' + path;
        if (options && options.length) {
            url += '?' + options.map(function(o) {
                if (typeof o === 'object') {
                    return Object.keys(o).map(function(k) {
                        return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]);
                    }).join('&');
                }
                return '';
            }).join('&');
        }
        const r = await fetch(url);
        return r.json();
    }
}

// ── 加载文件树 ──
async function loadTree(dirPath) {
    currentTreeDir = dirPath || '';
    let data;
    if (hasNativeApi) {
        data = await window.pywebview.api.get_tree(dirPath || '');
    } else {
        data = await api('/tree', [{dir: dirPath || ''}]);
    }
    document.getElementById('root-label').textContent = data.root;
    renderTree(document.getElementById('file-tree'), data.children, data.dir === '.' ? '' : data.dir, data.root);
    highlightActiveDrive();
}

// ── 加载盘符列表 ──
var cachedDriveElements = null;

async function loadDrives() {
    var drives;
    if (hasNativeApi) {
        var d = await window.pywebview.api.get_drives();
        drives = d.drives;
    } else {
        var d = await api('/drives');
        drives = d.drives;
    }
    var bar = document.getElementById('drives-bar');
    var html = '';
    drives.forEach(function(drv) {
        html += '<span class="drive-item" data-drive-path="' + escAttr(drv.path) + '" ' +
                'title="切换到 ' + escAttr(drv.path) + '">💿 ' +
                escHtml(drv.name) + '</span>';
    });
    bar.innerHTML = html;
    cachedDriveElements = null;
    highlightActiveDrive();
}

// 盘符点击事件委托
document.getElementById('drives-bar').addEventListener('click', function(e) {
    var el = e.target.closest('.drive-item');
    if (!el) return;
    switchDrive(el.dataset.drivePath, el.textContent.replace('💿 ', ''));
});

async function switchDrive(drivePath, driveName) {
    if (hasNativeApi) {
        await window.pywebview.api.change_root(drivePath);
        await loadTree('');
    } else {
        var r = await fetch('/api/change_root', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({root: drivePath})
        });
        var data = await r.json();
        if (data.error) { alert(data.error); return; }
        document.getElementById('root-label').textContent = data.root;
        renderTree(document.getElementById('file-tree'), data.children, '', data.root);
        currentTreeDir = '';
    }
    highlightActiveDrive();
}

function highlightActiveDrive() {
    var currentRoot = document.getElementById('root-label').textContent;
    if (!cachedDriveElements) {
        cachedDriveElements = document.querySelectorAll('.drive-item');
    }
    cachedDriveElements.forEach(function(el) {
        el.classList.toggle('active', currentRoot.startsWith(el.dataset.drivePath));
    });
}

// ── 渲染树节点（单层：.. + 目录 + 文件）──
function renderTree(container, children, currentDir, rootDir) {
    var html = '';
    rootDir = rootDir ? rootDir.replace(/\\/g, '/') : '';

    // ".." 返回上级（非根目录时切换到上级目录，根目录时切换到父目录）
    if (currentDir) {
        html += '<div class="tree-item up-dir" data-nav="up" data-dir="' +
                escAttr(currentDir) + '">' +
                '<span class="icon">📂</span><span class="name">.. （返回上级）</span></div>';
    } else if (rootDir) {
        html += '<div class="tree-item up-dir" data-nav="up-root" data-dir="' +
                escAttr(rootDir) + '">' +
                '<span class="icon">📂</span><span class="name">.. （返回上级）</span></div>';
    }

    if (!children || children.length === 0) {
        html += '<div class="empty-tree">📭 目录为空</div>';
        container.innerHTML = html;
        return;
    }

    for (var idx = 0; idx < children.length; idx++) {
        var item = children[idx];
        var relPath = currentDir ? currentDir + '/' + item.name : item.name;

        if (item.type === 'dir') {
            html += '<div class="tree-item folder" data-nav="dir" data-path="' +
                    escAttr(relPath) + '">' +
                    '<span class="icon">📁</span><span class="name">' +
                    escHtml(item.name) + '</span></div>';
        } else if (item.type === 'file') {
            var absPath = rootDir ? rootDir + '/' + relPath : relPath;
            var icon = item.is_md ? '📄' : '📃';
            html += '<div class="tree-item file-item" data-nav="file" data-path="' +
                    escAttr(absPath) + '" data-size="' +
                    escAttr(item.size_human) + '">' +
                    '<span class="icon">' + icon + '</span><span class="name">' +
                    escHtml(item.name) + '</span><span class="size">' +
                    escHtml(item.size_human) + '</span></div>';
        }
    }
    container.innerHTML = html;
}

function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function escAttr(s) {
    return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// 文件树点击事件委托
document.getElementById('file-tree').addEventListener('click', function(e) {
    var el = e.target.closest('.tree-item');
    if (!el) return;
    var nav = el.dataset.nav;
    if (nav === 'up') {
        var parts = el.dataset.dir.split('/');
        parts.pop();
        loadTree(parts.join('/'));
    } else if (nav === 'up-root') {
        navigateToParent(el.dataset.dir);
    } else if (nav === 'dir') {
        loadTree(el.dataset.path);
    } else if (nav === 'file') {
        openFile(el.dataset.path, el.dataset.size);
    }
});

async function navigateToParent(rootDir) {
    rootDir = rootDir.replace(/\\/g, '/');
    var isUnixAbs = rootDir.charAt(0) === '/' && rootDir.indexOf(':') === -1;
    var parts = rootDir.split('/').filter(function(p) { return p.length > 0; });
    if (parts.length === 0) return;
    parts.pop();
    var parentPath = parts.join('/');
    if (!parentPath) return;
    if (isUnixAbs) parentPath = '/' + parentPath;
    if (hasNativeApi) {
        await window.pywebview.api.change_root(parentPath);
        await loadTree('');
    } else {
        var r = await fetch('/api/change_root', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({root: parentPath})
        });
        var data = await r.json();
        if (data.error) { alert(data.error); return; }
        document.getElementById('root-label').textContent = data.root;
        renderTree(document.getElementById('file-tree'), data.children, '', data.root);
        currentTreeDir = '';
        highlightActiveDrive();
    }
}

// ── 大纲生成 ──
function buildOutline() {
    var list = document.getElementById('outline-list');
    var countEl = document.querySelector('#outline-header .count');
    var content = document.getElementById('content');
    var headings = content.querySelectorAll('h1, h2, h3, h4');

    if (headings.length === 0) {
        list.innerHTML = '<div class="outline-empty">无标题</div>';
        countEl.textContent = '';
        return;
    }

    countEl.textContent = '(' + headings.length + ')';
    list.innerHTML = '';

    headings.forEach(function(h, i) {
        var id = h.id || ('outline-' + i);
        if (!h.id) h.id = id;

        var level = parseInt(h.tagName.substring(1));
        var a = document.createElement('a');
        a.className = 'outline-item lv' + level;
        a.href = '#' + id;
        a.textContent = h.textContent;
        a.title = h.textContent;
        a.onclick = function(e) {
            e.preventDefault();
            h.scrollIntoView({behavior: 'smooth', block: 'start'});
            // 高亮
            list.querySelectorAll('.outline-item.active').forEach(function(el) { el.classList.remove('active'); });
            a.classList.add('active');
        };
        list.appendChild(a);
    });

    // 滚动监听：自动高亮当前可见标题
    if (outlineObserver) outlineObserver.disconnect();
    outlineObserver = new IntersectionObserver(function(entries) {
        entries.forEach(function(entry) {
            if (entry.isIntersecting) {
                var id = entry.target.id;
                list.querySelectorAll('.outline-item.active').forEach(function(el) { el.classList.remove('active'); });
                var active = list.querySelector('a[href="#' + id + '"]');
                if (active) active.classList.add('active');
            }
        });
    }, { rootMargin: '-80px 0px -60% 0px' });

    headings.forEach(function(h) { outlineObserver.observe(h); });
}

// ── 分隔条拖动 ──
(function() {
    var handle = document.getElementById('split-handle');
    var panel = document.getElementById('outline-panel');
    var startY, startH;
    handle.addEventListener('mousedown', function(e) {
        e.preventDefault();
        startY = e.clientY;
        startH = panel.offsetHeight;
        document.addEventListener('mousemove', onDrag);
        document.addEventListener('mouseup', onDragEnd);
    });
    function onDrag(e) {
        var newH = startH + (startY - e.clientY);
        newH = Math.max(40, Math.min(500, newH));
        panel.style.height = newH + 'px';
    }
    function onDragEnd() {
        document.removeEventListener('mousemove', onDrag);
        document.removeEventListener('mouseup', onDragEnd);
    }
})();

// ── 打开文件（支持相对路径或绝对路径） ──
async function openFile(filePath, sizeHuman) {
    // 高亮树节点
    highlightTreeNode(filePath);

    currentFilePath = filePath;
    document.getElementById('breadcrumb').textContent = '📄 ' + filePath;
    document.getElementById('status-file').textContent = filePath;
    document.getElementById('content').innerHTML = '<div class="welcome"><p>加载中...</p></div>';

    var data;
    if (hasNativeApi) {
        // 通过原生 API（支持绝对路径，如拖放的外部文件）
        data = await window.pywebview.api.open_external_file(filePath);
    } else {
        data = await api('/file', [{path: filePath}]);
    }

    if (data.error) {
        document.getElementById('content').innerHTML = '<div class="welcome"><p style="color:red">❌ ' + escHtml(data.error) + '</p></div>';
        return;
    }

    document.getElementById('content').innerHTML = data.html;
    document.getElementById('status-lines').textContent = sizeHuman || data.size || '';
    buildOutline();

    // 如果根目录变了，重载文件树后用 API 返回的绝对路径重新高亮
    if (data.root_changed && hasNativeApi) {
        document.getElementById('root-label').textContent = data.new_root;
        await loadTree('');
        highlightTreeNode(data.path);
    }

    document.getElementById('content').scrollTop = 0;
}

// 公共高亮辅助：在文件树中匹配 filePath 并高亮
function highlightTreeNode(path) {
    if (!path) return;
    document.querySelectorAll('.tree-item.active').forEach(function(el) { el.classList.remove('active'); });
    var norm = path.replace(/\\/g, '/');
    var node = document.querySelector('#file-tree [data-path="' + norm.replace(/"/g, '\\"') + '"]');
    if (node) node.classList.add('active');
}

// ── 📂 打开文件按钮 ──
async function btnOpenFile() {
    if (hasNativeApi) {
        var data = await window.pywebview.api.open_file_dialog();
        if (data.cancelled) return;
        if (data.error) { alert(data.error); return; }
        document.getElementById('content').innerHTML = data.html;
        document.getElementById('breadcrumb').textContent = '📄 ' + data.path;
        document.getElementById('status-file').textContent = data.path;
        document.getElementById('status-lines').textContent = data.size || '';
        buildOutline();
        if (data.root_changed) {
            document.getElementById('root-label').textContent = data.new_root;
            await loadTree('');
        }
        highlightTreeNode(data.path);
        document.getElementById('content').scrollTop = 0;
    } else {
        // 浏览器模式：使用隐藏的 file input
        document.getElementById('file-input').click();
    }
}

// ── 浏览器模式文件 input 回调 ──
function onFileInputChange(e) {
    var file = e.target.files[0];
    if (!file) return;
    renderFromFileObject(file, file.name);
    e.target.value = '';
}

// ── 从 File 对象渲染（浏览器模式拖放/文件选择共用） ──
function renderFromFileObject(file, displayName) {
    document.getElementById('breadcrumb').textContent = '📄 ' + displayName;
    document.getElementById('status-file').textContent = displayName;
    document.getElementById('status-lines').textContent = formatSize(file.size);
    document.getElementById('content').innerHTML = '<div class="welcome"><p>加载中...</p></div>';

    var reader = new FileReader();
    reader.onload = function(ev) {
        var text = ev.target.result;
        // 通过 HTTP API 让服务端渲染（利用内置渲染器/markdown 库）
        fetch('/api/render?name=' + encodeURIComponent(file.name), {
            method: 'POST',
            headers: {'Content-Type': 'text/plain; charset=utf-8'},
            body: text
        }).then(function(r) { return r.json(); })
          .then(function(data) {
              if (data.error) {
                  document.getElementById('content').innerHTML =
                      '<div class="welcome"><p style="color:red">❌ ' + escHtml(data.error) + '</p></div>';
              } else {
                  document.getElementById('content').innerHTML = data.html;
                  buildOutline();
              }
          }).catch(function(err) {
              document.getElementById('content').innerHTML =
                  '<div class="welcome"><p style="color:red">❌ 渲染失败: ' + escHtml(err.message) + '</p></div>';
          });
    };
    reader.readAsText(file, 'utf-8');
}

function formatSize(bytes) {
    if (!bytes) return '';
    var units = ['B', 'KB', 'MB', 'GB'];
    var i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
    return bytes.toFixed(0) + ' ' + units[i];
}

// ── 拖放支持 ──
var mainEl = document.getElementById('main');
var dropOverlay = document.getElementById('drop-overlay');
var dragCounter = 0;

mainEl.addEventListener('dragenter', function(e) {
    e.preventDefault(); e.stopPropagation();
    dragCounter++;
    if (dragCounter === 1) dropOverlay.classList.add('show');
});

mainEl.addEventListener('dragleave', function(e) {
    e.preventDefault(); e.stopPropagation();
    dragCounter--;
    if (dragCounter === 0) dropOverlay.classList.remove('show');
});

mainEl.addEventListener('dragover', function(e) {
    e.preventDefault(); e.stopPropagation();
});

mainEl.addEventListener('drop', function(e) {
    e.preventDefault(); e.stopPropagation();
    dragCounter = 0;
    dropOverlay.classList.remove('show');

    var filepath = null;
    var fileObj = null;

    // 方式 1：dataTransfer.files（WebView2/Electron）
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        var f = e.dataTransfer.files[0];
        filepath = f.path || null;
        fileObj = f;  // 浏览器模式备用
    }

    // 方式 2：text/uri-list
    if (!filepath) {
        var uri = e.dataTransfer.getData('text/uri-list');
        if (uri) {
            uri = uri.trim().split('\n')[0].replace(/^file:\/\//, '');
            filepath = decodeURIComponent(uri);
        }
    }

    // 方式 3：text/plain
    if (!filepath) {
        var text = e.dataTransfer.getData('text/plain');
        if (text) {
            text = text.trim().replace(/^file:\/\//, '');
            if (/\.md$/i.test(text)) filepath = text;
        }
    }

    // 有路径 → 走 API（桌面模式）
    if (filepath) {
        filepath = filepath.replace(/\\/g, '/');
        openFile(filepath, '');
        return;
    }

    // 无路径但有文件对象 → 浏览器模式 FileReader
    if (fileObj) {
        renderFromFileObject(fileObj, fileObj.name);
        return;
    }

    alert('无法获取文件，请使用 📂 打开按钮');
});

// ── 主题切换 ──
function getTheme() {
    return localStorage.getItem('mdviewer-theme') || 'light';
}
function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    document.getElementById('theme-btn').textContent = theme === 'dark' ? '☀️' : '🌙';
    localStorage.setItem('mdviewer-theme', theme);
}
function toggleTheme() {
    var current = getTheme();
    applyTheme(current === 'dark' ? 'light' : 'dark');
}

// ── 键盘快捷键 ──
document.addEventListener('keydown', function(e) {
    if (e.ctrlKey && e.key === 'o') {
        e.preventDefault();
        btnOpenFile();
    } else if (e.ctrlKey && e.key === 'q') {
        e.preventDefault();
        if (hasNativeApi && window.pywebview && window.pywebview.api) {
            // PyWebView 模式：通知 Python 退出
            window.close();
        } else {
            // 浏览器模式：关闭标签页
            window.close();
        }
    }
});

// ── 初始化 ──
async function init() {
    applyTheme(getTheme());
    await Promise.all([loadDrives(), loadTree('')]);
    var params = new URLSearchParams(window.location.search);
    var initialFile = params.get('open');
    if (initialFile) {
        openFile(initialFile, '');
    }
}
init();
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════
#  HTTP API 服务器（无 pywebview 时的后备方案）
# ═══════════════════════════════════════════════════════════

class APIHandler(SimpleHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        try:
            if path == "/" or path == "/index.html":
                self._send_html(APP_HTML)
            elif path == "/api/drives":
                self._send_json({"drives": find_drives()})
            elif path == "/api/tree":
                self._handle_tree(parsed.query)
            elif path == "/api/file":
                self._handle_file(parsed.query)
            else:
                self.send_error(404)
        except Exception as e:
            self.send_error(500, str(e))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        if path == "/api/render":
            length = int(self.headers.get("Content-Length", 0))
            text = self.rfile.read(length).decode("utf-8", errors="replace")
            params = urllib.parse.parse_qs(parsed.query)
            fname = params.get("name", [""])[0]
            if fname.lower().endswith(".md"):
                try:
                    import markdown as md_lib
                    html = md_lib.markdown(text, extensions=["fenced_code", "tables"])
                except ImportError:
                    html = md_to_html(text)
            else:
                escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                html = f"<pre><code>{escaped}</code></pre>"
            self._send_json({"html": html})
        elif path == "/api/change_root":
            global ROOT_DIR
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            p = Path(body.get("root", "")).resolve()
            if p.is_dir():
                ROOT_DIR = p
                children = build_tree(ROOT_DIR)
                self._send_json({"root": str(ROOT_DIR), "children": children})
            else:
                self._send_json({"error": "无效目录"})
        else:
            self.send_error(404)

    def _handle_tree(self, query: str):
        params = urllib.parse.parse_qs(query)
        dir_rel = params.get("dir", [""])[0]
        safe = os.path.normpath(dir_rel)
        full = (ROOT_DIR / safe).resolve()
        try:
            full.relative_to(ROOT_DIR.resolve())
        except ValueError:
            self._send_json({"error": "禁止访问"})
            return
        if not full.is_dir():
            self._send_json({"error": "不是目录"})
            return
        children = build_tree(full)
        self._send_json({"root": str(ROOT_DIR), "dir": safe, "children": children})

    def _handle_file(self, query: str):
        params = urllib.parse.parse_qs(query)
        file_rel = params.get("path", [""])[0]
        safe = os.path.normpath(file_rel)
        full = (ROOT_DIR / safe).resolve()
        try:
            full.relative_to(ROOT_DIR.resolve())
        except ValueError:
            self._send_json({"error": "禁止访问"})
            return
        if not full.is_file():
            self._send_json({"error": "不是文件"})
            return
        html_body = render_file(full)
        self._send_json({"path": safe, "name": full.name, "html": html_body, "size": get_file_size(full)})

    def _send_html(self, html: str):
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


def start_server():
    HTTPServer((HOST, PORT), APIHandler).serve_forever()


def _wait_for_server(host, port, timeout=2.0):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            s = socket.create_connection((host, port), timeout=0.05)
            s.close()
            return
        except OSError:
            time.sleep(0.01)


# ═══════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════

def main():
    global ROOT_DIR

    # 设置 stdout 为 UTF-8（避免 Windows GBK 下 emoji 崩溃）
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    initial_file = None
    if len(sys.argv) > 1:
        target = Path(sys.argv[1]).resolve()
        if target.is_dir():
            ROOT_DIR = target
        elif target.is_file() and _is_text_file(target.name):
            ROOT_DIR = target.parent
            initial_file = target.name
        elif target.is_file():
            ROOT_DIR = target.parent
        else:
            print(f"[X] invalid path: {sys.argv[1]}")
            sys.exit(1)

    print(f"[*] Root: {ROOT_DIR}")

    # 启动 HTTP 服务
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    _wait_for_server(HOST, PORT)

    url = f"http://{HOST}:{PORT}"
    if initial_file:
        url += f"?open={urllib.parse.quote(initial_file)}"

    # 启动桌面窗口
    try:
        import webview
        api = MdApi()
        print(f"[*] Starting desktop window: {url}")
        window = webview.create_window(
            title="MDViewer — Markdown Browser",
            url=url,
            width=1200, height=800, min_size=(700, 400),
            text_select=True, js_api=api,
        )
        api.set_window(window)
        webview.start()
    except Exception as e:
        # PyWebView 启动失败 -> 降级到浏览器模式
        print(f"[!] Desktop window failed: {e}")
        print(f"    Falling back to browser: {url}")
        import webbrowser
        webbrowser.open(url)
        print("    Press Ctrl+C to exit...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nBye")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # --windowed 模式下无控制台，用弹窗显示错误
        import traceback
        err = traceback.format_exc()
        try:
            import tkinter.messagebox as mb
            mb.showerror("MDViewer 启动失败", err)
        except Exception:
            pass
        # 同时写日志文件
        try:
            with open("mdviewer_error.log", "w", encoding="utf-8") as f:
                f.write(err)
        except Exception:
            pass
        sys.exit(1)
