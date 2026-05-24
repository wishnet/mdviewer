# MDViewer — 桌面 Markdown / 文本文件浏览器

独立窗口运行的文件浏览器，嵌入式系统 WebView，支持 Markdown 渲染和纯文本浏览。

## 功能

| 功能 | 说明 |
|------|------|
| 📁 文件树导航 | 单层目录浏览，单击目录进入、`..` 返回上级 |
| 💿 磁盘切换 | 自动检测本地盘符（C:/D: 等），一键切换 |
| 📄 Markdown 渲染 | GitHub 风格排版，支持表格、代码块、任务列表 |
| 📝 纯文本浏览 | 支持 40+ 种文本格式（.py .js .json .txt .log 等） |
| 🎨 代码高亮 | 需 markdown + Pygments（安装后自动启用） |
| 🔤 编码检测 | 自动识别 UTF-8/GBK/Big5/Shift-JIS 等编码 |
| 🌙 日间/夜间模式 | 一键切换，偏好自动保存 |
| 📑 文件大纲 | 自动提取 h1~h4 标题，点击跳转，滚动高亮 |
| 📂 打开文件 | 按钮 / Ctrl+O / 拖放三种方式 |
| ⌨️ 快捷键 | Ctrl+O 打开文件，Ctrl+Q 退出 |
| 🖱️ 拖放打开 | 从资源管理器拖入文件直接浏览 |
| 📦 打包为 EXE | PyInstaller 一键打包为单文件 |

## 安装

```bash
pip install -r requirements.txt
```

> 仅 `pywebview` 为必需依赖。`markdown`/`Pygments` 为可选依赖，用于增强代码高亮。`chardet` 可选，用于提高编码检测准确率。

## 使用

```bash
# 浏览当前目录
python mdviewer.py

# 浏览指定目录
python mdviewer.py D:\docs

# 打开指定文件
python mdviewer.py D:\docs\README.md
```

启动后弹出独立桌面窗口。如 `pywebview` 未安装，自动降级为浏览器模式。

## 打包为 EXE

```bash
# 双击运行（需先安装 pyinstaller）
build.bat

# 或手动执行
pyinstaller --onefile --noconsole --icon=icon.ico --name MDViewer mdviewer.py
```

输出：`dist\MDViewer.exe`

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+O` | 打开文件 |
| `Ctrl+Q` | 退出程序 |

## 界面布局

```
┌──────────────┬──────────────────────────┐
│ 💿 C:  💿 D: │ 📄 README.md    [🌙][📂][🔄]│
├──────────────┤                          │
│ 📁 /mnt/d/.. │                          │
├──────────────┤  # 标题一                 │
│ 📂 .. (返回)  │                          │
│ 📁 子目录     │  正文内容...              │
│ 📄 README.md │                          │
│ 📄 config.py │  ```代码块```             │
├──────────────┤                          │
│══ 拖动调整 ══│                          │
├──────────────┤                          │
│ 📑 大纲 (12) │                          │
│ 标题一        │                          │
│  标题二       │                          │
└──────────────┴──────────────────────────┘
```

## 依赖

| 依赖 | 必需 | 用途 |
|------|------|------|
| pywebview | ✅ | 桌面窗口 + 嵌入式 WebView |
| markdown | ❌ | 完整 Markdown 渲染 |
| pymdown-extensions | ❌ | Markdown 扩展（代码高亮等） |
| Pygments | ❌ | 代码语法高亮 |
| chardet | ❌ | 文件编码检测 |

## 项目结构

```
mdviewer/
├── mdviewer.py        # 主程序
├── build.bat          # EXE 打包脚本
├── requirements.txt   # Python 依赖
├── icon.ico           # 程序图标
├── README.md          # 说明文档
└── .gitignore
```
