# AGENTS.md

## Project overview

Single-file Python desktop app (`mdviewer.py`, ~1400 lines). Uses **PyWebView** to embed an Edge WebView2 window. The entire frontend HTML/CSS/JS is a Python raw string literal (`APP_HTML`). No Node.js, no build pipeline, no multi-file source tree.

## Run / test

```
python mdviewer.py                  # browse CWD
python mdviewer.py D:/docs          # browse specific directory
python mdviewer.py path/to/README.md  # open specific file
```

There is no test suite. Manual verification: open a .md file, toggle theme, drag-and-drop a file, navigate the tree.

## Build

```
build.bat             # PyInstaller -> dist/MDViewer.exe
```

CI triggers on `v*` tags or manual dispatch (`workflow_dispatch`). Uses GitHub Actions windows-latest with Python 3.11.

## Architecture

| Section | Lines | Role |
|---------|-------|------|
| Markdown renderer (built-in) | 35–215 | `md_to_html()`, `_inline_format()`, `_render_table()` — zero-dep regex renderer, fallback when `markdown` lib missing |
| `markdown` lib (optional) | 263–266 | If installed, `render_file()` uses `markdown` + `codehilite` + `toc`; otherwise falls back to built-in |
| File ops | 218–310 | Encoding detection, `build_tree()`, `get_file_size()` |
| JS-Python API bridge | ~380–432 | `MdApi` class exposed as `window.pywebview.api` in JS |
| HTML SPA | 438–1212 | `APP_HTML` raw string — full frontend |
| HTTP API server | 1215–1325 | `APIHandler` — fallback when pywebview unavailable (browser mode) |
| Main entry | 1328–1417 | `main()` |

### Data flow

- **Desktop mode**: JS calls `window.pywebview.api.method(args)` → `MdApi` method → returns dict
- **Browser fallback**: JS calls `fetch('/api/...')` → `APIHandler` HTTP handler → JSON response
- JS checks `!!window.pywebview?.api` at runtime to decide which path to use

## Key conventions

- **Regex are precompiled** as module-level `_RE_*` constants. Always compile new markdown regex at module level, not inside functions.
- **`_detect_encoding()` takes raw bytes**, not a Path — avoids double file I/O with `render_file()`.
- **`build_tree()` passes size to `_format_size()`** directly rather than calling `get_file_size()` which would `stat()` again.
- **Frontend event delegation**: file-tree and drive-bar use single container-level `addEventListener('click', ...)` with `e.target.closest()`. Do not attach per-node `onclick` handlers.
- **`outlineObserver`** is a module-level variable. `buildOutline()` must disconnect it before creating a new `IntersectionObserver`.
- **`cachedDriveElements`** stores the queried NodeList to avoid repeated `querySelectorAll('.drive-item')`.
- **Path handling — CRITICAL**: 
  - Tree file nodes store **absolute paths** in `data-path` (built in `renderTree` as `rootDir + '/' + relPath`).
  - Tree directory nodes store **relative paths** in `data-path` (used by `loadTree()` for navigation).
  - `highlightTreeNode(path)` normalizes backslashes to forward slashes before DOM query.
  - Python `open_external_file()` resolves relative paths against `ROOT_DIR` before passing to `_open_and_render()`.
  - `openFile()` calls `highlightTreeNode(data.path)` after `root_changed` → `loadTree('')` to ensure correct highlight in the reloaded tree.

## Dependencies

- **Required**: `pywebview>=4.0`
- **Optional** (better rendering): `markdown>=3.4`, `pymdown-extensions>=10.0`, `Pygments>=2.15`
- Unused import `time` is imported but only for `sleep()` in main.
