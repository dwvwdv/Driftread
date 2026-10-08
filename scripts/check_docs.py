#!/usr/bin/env python3
"""文件一致性檢查（CI：.github/workflows/docs.yml）。

規則的來由見 AGENTS.md 的「文件維護」：
- CLAUDE.md 只能是 `@AGENTS.md`（AGENTS.md 是唯一的規範來源）
- AGENTS.md 不得超過 30 KiB（Codex 預設只讀前 32 KiB）
- 所有 Markdown 文件的相對連結都必須指向存在的檔案或目錄
- AGENTS.md 以反引號提到的路徑都必須存在
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

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

# 連結與圖片都檢查：`[說明](a.md)`、`![圖](img.png)`、`[說明](a.md "標題")`、`[說明](<a b.md>)`
LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*(<[^>\n]+>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
CODE_RE = re.compile(r"`([^`\n]+)`")
# CommonMark 的程式碼圍欄：最多三個空格縮排、``` 或 ~~~（至少三個），以同字元且不短於開頭的圍欄結束
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def strip_code_fences(text: str) -> str:
    """把圍欄內的範例拿掉，避免示範語法被當成真正的連結或路徑。"""
    kept, fence = [], None
    for line in text.split("\n"):
        match = FENCE_OPEN_RE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
                continue
            kept.append(line)
        elif match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) \
                and not line.strip()[len(match.group(1)):].strip():
            fence = None
    return "\n".join(kept)


def markdown_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*.md"):
        if SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return sorted(files)


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
    # 行內程式碼裡的 `[說明](a.md)` 是示範語法，不是連結
    text = CODE_RE.sub("", strip_code_fences(path.read_text(encoding="utf-8")))
    for target in LINK_RE.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target.lstrip("<"), re.I) or target.startswith("#"):
            continue
        target = target.strip("<>")
        file_part = target.split("#", 1)[0]
        if not file_part:
            continue
        if not (path.parent / file_part).exists():
            errors.append(f"{path.relative_to(ROOT)}: 連結指向不存在的路徑 {target}")
    return errors


def looks_like_path(token: str) -> bool:
    if any(ch in token for ch in " *<>{}$=|,()"):
        return False
    if token.startswith(("-", "/", "http", "@", "~")):
        return False
    return "/" in token or token.endswith(PATH_SUFFIXES)


def check_code_paths(path: Path) -> list[str]:
    errors = []
    text = strip_code_fences(path.read_text(encoding="utf-8"))
    for token in CODE_RE.findall(text):
        token = token.split("::", 1)[0].rstrip("/")
        if not looks_like_path(token):
            continue
        if not any((root / token).exists() for root in PATH_ROOTS):
            errors.append(f"{path.relative_to(ROOT)}: 提到的路徑不存在 `{token}`")
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
