#!/usr/bin/env python3
"""Generate .env with required secrets.

Usage:
    python3 scripts/gen_env.py

Fills in any empty variables in .env (or creates it from .env.example).
SUPABASE_URL and SUPABASE_KEY must be filled in manually from the Supabase Dashboard.
"""
import os
import secrets


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
ENV_FILE = os.path.join(REPO_ROOT, ".env")
ENV_EXAMPLE = os.path.join(REPO_ROOT, ".env.example")


def read_env(path: str) -> dict[str, str]:
    result = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip()
    return result


def main():
    if not os.path.exists(ENV_FILE):
        import shutil
        shutil.copy(ENV_EXAMPLE, ENV_FILE)
        print(f".env created from .env.example — fill in SUPABASE_URL and SUPABASE_KEY")

    existing = read_env(ENV_FILE)

    generated = {
        "ADMIN_API_KEY": lambda: secrets.token_urlsafe(24),
    }

    lines = open(ENV_FILE).readlines()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, val = stripped.partition("=")
            key = key.strip()
            if key in generated and not val.strip():
                value = generated[key]()
                new_lines.append(f"{key}={value}\n")
                print(f"  generated {key}")
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    with open(ENV_FILE, "w") as f:
        f.writelines(new_lines)

    missing = [k for k in ("SUPABASE_URL", "SUPABASE_KEY") if not existing.get(k)]
    if missing:
        print(f"\n⚠ 請手動填入 .env 中的：{', '.join(missing)}")
        print("  Supabase Dashboard → Settings → API")


if __name__ == "__main__":
    main()
