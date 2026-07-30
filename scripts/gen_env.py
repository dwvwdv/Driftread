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

# Autonomous discovery knobs. Same deal as above — backend/services/
# discovery_config.py supplies these defaults — except this loop ships OFF, so
# the reporting below leads with that rather than listing every value.
OPTIONAL_DISCOVERY_DEFAULTS = {
    "FEED_DISCOVERY_ENABLED": "false",
    "FEED_DISCOVERY_TICK_SECONDS": "900",
    "FEED_DISCOVERY_HARVEST_BATCH_SIZE": "10",
    "FEED_DISCOVERY_HARVEST_ARTICLES": "20",
    "FEED_DISCOVERY_HARVEST_INTERVAL_HOURS": "168",
    "FEED_DISCOVERY_HARVEST_MAX_LINKS_PER_FEED": "200",
    "FEED_DISCOVERY_BLOGROLL_ENABLED": "false",
    "FEED_DISCOVERY_DIRECTORY_ENABLED": "false",
    "FEED_DISCOVERY_DIRECTORY_BATCH_SIZE": "3",
    "FEED_DISCOVERY_PROBE_BATCH_SIZE": "20",
    "FEED_DISCOVERY_PROBE_CONCURRENCY": "3",
    "FEED_DISCOVERY_PROBE_MAX_ATTEMPTS": "3",
    "FEED_DISCOVERY_PROBE_RETRY_HOURS": "24",
    "FEED_DISCOVERY_HOST_DELAY_SECONDS": "2",
    "FEED_DISCOVERY_RESPECT_ROBOTS": "true",
    "FEED_DISCOVERY_MAX_FRONTIER_SIZE": "50000",
    "FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS": "0",
}

# Read by backend/worker.py. Optional, with a code-level default.
OPTIONAL_MISC_DEFAULTS = {
    "LOG_LEVEL": "INFO",
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

    # Autonomous discovery. Report the on/off state unconditionally rather than
    # only when nothing is set: whether the crawler is running is the one thing
    # an operator should never have to go digging for.
    discovery_flag = existing.get("FEED_DISCOVERY_ENABLED", "").strip().lower()
    if discovery_flag and discovery_flag not in ("0", "false", "no", "off"):
        print("\n⚠ 自主發現已啟用（FEED_DISCOVERY_ENABLED=%s）。" % discovery_flag)
        print("  這個迴圈會主動對第三方網站發出請求。請確認你已讀過")
        print("  docs/SECURITY.md 的自主發現章節，特別是 DNS rebinding 一節，")
        print("  並在 DISCOVERY_USER_AGENT 帶上聯絡網址。")
    else:
        print("\nℹ 自主發現預設關閉（FEED_DISCOVERY_ENABLED=false）；")
        print("  啟用前請先讀 docs/SECURITY.md。其餘 FEED_DISCOVERY_* 參數")
        print("  也都有預設值，見 .env.example。")

    unset_misc = [k for k in OPTIONAL_MISC_DEFAULTS if k not in existing]
    if unset_misc:
        print("\nℹ 其他可選變數使用預設值：")
        for key in unset_misc:
            print(f"    {key}={OPTIONAL_MISC_DEFAULTS[key]}")


if __name__ == "__main__":
    main()
