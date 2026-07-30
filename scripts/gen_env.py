#!/usr/bin/env python3
"""Generate .env with required secrets.

Usage:
    python3 scripts/gen_env.py

Fills in any empty variables in .env (or creates it from .env.example).
SUPABASE_URL / SUPABASE_KEY (service_role) / DATABASE_URL / SUPABASE_JWT_SECRET
must be filled in manually from the Supabase Dashboard.
"""
import os
import secrets


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
ENV_FILE = os.path.join(REPO_ROOT, ".env")
ENV_EXAMPLE = os.path.join(REPO_ROOT, ".env.example")

# Feed refresh scheduler knobs. Optional — backend/services/feed_refresh.py
# supplies these same defaults when the variables are absent, so they're
# reported rather than required.
OPTIONAL_REFRESH_DEFAULTS = {
    "FEED_REFRESH_ENABLED": "true",
    "FEED_REFRESH_TICK_SECONDS": "300",
    "FEED_REFRESH_BATCH_SIZE": "50",
    "FEED_REFRESH_CONCURRENCY": "5",
    "FEED_REFRESH_MIN_INTERVAL_MINUTES": "15",
    "FEED_REFRESH_MAX_INTERVAL_MINUTES": "1440",
}


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

    PLACEHOLDERS = {
        "SUPABASE_URL": "https://your-project-id.supabase.co",
        "SUPABASE_KEY": "your-service-role-key",
        "DATABASE_URL": "postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:5432/postgres",
    }

    def is_unset(key: str) -> bool:
        val = existing.get(key, "").strip()
        return not val or val == PLACEHOLDERS.get(key)

    if is_unset("SUPABASE_URL") or is_unset("SUPABASE_KEY"):
        missing = [k for k in ("SUPABASE_URL", "SUPABASE_KEY") if is_unset(k)]
        print(f"\n⚠ 請手動填入 .env 中的：{', '.join(missing)}")
        print("  Supabase Dashboard → Settings → API")
        if "SUPABASE_KEY" in missing:
            print("  ↳ SUPABASE_KEY 必須是 service_role key（不是 anon key），")
            print("    backend 需要繞過 RLS 進行 admin 寫入。")

    if is_unset("DATABASE_URL"):
        print("\n⚠ 請手動填入 .env 中的：DATABASE_URL")
        print("  Supabase Dashboard → Settings → Database → Connection string (URI)")

    if is_unset("SUPABASE_JWT_SECRET"):
        print("\n⚠ 請手動填入 .env 中的：SUPABASE_JWT_SECRET")
        print("  Supabase Dashboard → Settings → API → JWT Settings → JWT Secret")

    # Feed refresh scheduler settings. All have code-level defaults (see
    # backend/services/feed_refresh.py), so they're deliberately not part of the
    # missing check above — an unset .env still runs. Just report the effective
    # values so it's clear the scheduler is on and at what cadence.
    if all(k not in existing for k in OPTIONAL_REFRESH_DEFAULTS):
        print("\nℹ 自動抓取排程使用預設值（可在 .env 覆寫，見 .env.example）：")
        for key, default in OPTIONAL_REFRESH_DEFAULTS.items():
            print(f"    {key}={default}")


if __name__ == "__main__":
    main()
