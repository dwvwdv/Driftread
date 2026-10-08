#!/usr/bin/env python3
"""文件一致性檢查（CI：.github/workflows/docs.yml）。

規則的來由見 AGENTS.md 的「文件維護」：
- CLAUDE.md 只能是 `@AGENTS.md`（AGENTS.md 是唯一的規範來源）
- AGENTS.md 不得超過 30 KiB（Codex 預設只讀前 32 KiB）
- 所有 Markdown 文件的相對連結與圖片都必須指向存在的檔案或目錄
- AGENTS.md 以反引號提到的路徑都必須存在

Markdown 一律交給 CommonMark 解析器（markdown-it-py）處理，不自己用 regex 判斷：
圍欄／縮排程式碼區塊、行內程式碼、reference-style 連結、帶標題或角括號的目標，
解析器都已依規格處理好。依賴版本釘在 scripts/requirements-docs.txt。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token

ROOT = Path(__file__).resolve().parent.parent
AGENTS_MAX_BYTES = 30 * 1024
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

MD = MarkdownIt("commonmark").enable("table")


def markdown_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*.md"):
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


def check_links(path: Path) -> list[str]:
    errors = []
    for token in inline_tokens(path):
        if token.type == "link_open":
            target = str(token.attrGet("href") or "")
        elif token.type == "image":
            target = str(token.attrGet("src") or "")
        else:
            continue
        if not target or target.startswith("#") or SCHEME_RE.match(target):
            continue
        file_part = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if file_part and not (path.parent / file_part).exists():
            errors.append(f"{path.relative_to(ROOT)}: 連結指向不存在的路徑 {file_part}")
    return errors


def looks_like_path(text: str) -> bool:
    if any(ch in text for ch in " *<>{}$=|,()"):
        return False
    if text.startswith(("-", "/", "http", "@", "~")):
        return False
    return "/" in text or text.endswith(PATH_SUFFIXES)


def check_code_paths(path: Path) -> list[str]:
    errors = []
    for token in inline_tokens(path):
        if token.type != "code_inline":
            continue
        text = token.content.split("::", 1)[0].rstrip("/")
        if not looks_like_path(text):
            continue
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
