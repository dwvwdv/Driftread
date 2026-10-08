#!/usr/bin/env python3
"""文件一致性檢查（CI：.github/workflows/docs.yml）。

規則的來由見 AGENTS.md 的「文件維護」：
- CLAUDE.md 只能是 `@AGENTS.md`（AGENTS.md 是唯一的規範來源）
- AGENTS.md 不得超過 30 KiB（Codex 預設只讀前 32 KiB）
- 所有 Markdown 文件的相對連結與圖片（含內嵌 HTML 的 href／src）都必須指向 repo 內存在的檔案或目錄
- AGENTS.md 以反引號提到的路徑都必須存在

Markdown 一律交給 CommonMark 解析器（markdown-it-py）處理，不自己用 regex 判斷：
圍欄／縮排程式碼區塊、行內程式碼、reference-style 連結、帶標題或角括號的目標，
解析器都已依規格處理好。依賴版本釘在 scripts/requirements-docs.txt。
"""
from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token

ROOT = Path(__file__).resolve().parent.parent
AGENTS_MAX_BYTES = 30 * 1024
MARKDOWN_SUFFIXES = {".md", ".markdown"}
SKIP_DIRS = {".git", "node_modules", ".angular", "dist", ".venv", "venv", "__pycache__"}

# 反引號裡的相對路徑會依序對這些根目錄解析（文件習慣省略 backend/、docs/ 或 frontend/src/app/ 前綴）
PATH_ROOTS = [
    ROOT,
    ROOT / "backend",
    ROOT / "docs",
    ROOT / "frontend",
    ROOT / "frontend/src/app",
    ROOT / "frontend/public",
]
PATH_SUFFIXES = (".py", ".ts", ".scss", ".html", ".sql", ".md", ".json", ".yml", ".yaml", ".toml", ".js", ".sh")
SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
DOTFILE_RE = re.compile(r"^\.[\w-]+\.[\w.-]+$")

MD = MarkdownIt("commonmark").enable("table")


def markdown_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if path.suffix.lower() not in MARKDOWN_SUFFIXES or not path.is_file():
            continue
        if SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return sorted(files)


def inline_tokens(path: Path) -> list[Token]:
    """回傳文件裡所有行內 token；程式碼區塊本身不是 inline，自然不會出現。"""
    tokens = []
    for block in MD.parse(path.read_text(encoding="utf-8")):
        if block.type == "inline" and block.children:
            tokens.extend(block.children)
    return tokens


def block_tokens(path: Path) -> list[Token]:
    return MD.parse(path.read_text(encoding="utf-8"))


class _HtmlTargets(HTMLParser):
    """收集內嵌 HTML（`<a href>`、`<img src>` 等）裡的連結目標。"""

    def __init__(self) -> None:
        super().__init__()
        self.targets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.targets += [value for name, value in attrs if name in ("href", "src") and value]


def html_targets(html: str) -> list[str]:
    parser = _HtmlTargets()
    parser.feed(html)
    return parser.targets


def check_claude_md() -> list[str]:
    content = (ROOT / "CLAUDE.md").read_text(encoding="utf-8").strip()
    if content != "@AGENTS.md":
        return ["CLAUDE.md 只能有一行 `@AGENTS.md`；規範請寫進 AGENTS.md"]
    return []


def check_agents_size() -> list[str]:
    size = (ROOT / "AGENTS.md").stat().st_size
    if size > AGENTS_MAX_BYTES:
        return [f"AGENTS.md 為 {size} bytes，超過 {AGENTS_MAX_BYTES}；細節請移到 docs/"]
    return []


def link_targets(path: Path) -> list[str]:
    targets = []
    for block in block_tokens(path):
        if block.type == "html_block":
            targets += html_targets(block.content)
    for token in inline_tokens(path):
        if token.type == "link_open":
            targets.append(str(token.attrGet("href") or ""))
        elif token.type == "image":
            targets.append(str(token.attrGet("src") or ""))
        elif token.type == "html_inline":
            targets += html_targets(token.content)
    return targets


def check_links(path: Path) -> list[str]:
    errors = []
    for target in link_targets(path):
        # 外部網址（含 `//host/...` 這種協定相對網址）與頁內錨點不檢查
        if not target or target.startswith(("#", "//")) or SCHEME_RE.match(target):
            continue
        file_part = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not file_part:
            continue
        resolved = (path.parent / file_part).resolve()
        # GitHub 上逸出 repo 的連結一定是壞的，即使 runner 上剛好有那個父目錄
        if not resolved.is_relative_to(ROOT):
            errors.append(f"{path.relative_to(ROOT)}: 連結逸出 repository {file_part}")
        elif not resolved.exists():
            errors.append(f"{path.relative_to(ROOT)}: 連結指向不存在的路徑 {file_part}")
    return errors


def looks_like_path(text: str) -> bool:
    if any(ch in text for ch in " *<>{}$=|,()"):
        return False
    if text.startswith(("-", "/", "http", "@", "~")):
        return False
    # `.env.example` 這類帶副檔名的 dotfile 也算；單純的 `.env`（gitignore、不在 repo 裡）不算
    return "/" in text or text.endswith(PATH_SUFFIXES) or bool(DOTFILE_RE.match(text))


def check_code_paths(path: Path) -> list[str]:
    errors = []
    for token in inline_tokens(path):
        if token.type != "code_inline":
            continue
        text = token.content.split("::", 1)[0]
        # 先判斷再去掉尾斜線：`docs/` 這種單層目錄靠尾斜線才認得出是路徑
        if not looks_like_path(text):
            continue
        text = text.rstrip("/")
        if not any((root / text).exists() for root in PATH_ROOTS):
            errors.append(f"{path.relative_to(ROOT)}: 提到的路徑不存在 `{text}`")
    return errors


def main() -> int:
    errors = check_claude_md() + check_agents_size()
    for path in markdown_files():
        errors += check_links(path)
    errors += check_code_paths(ROOT / "AGENTS.md")
    for error in errors:
        print(f"::error::{error}" if "--github" in sys.argv else error)
    if not errors:
        print("docs OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
