"""ClawHub / OpenClaw Skills Manager for Sayri.

Allows Sayri to discover, download, list, and read skills from ClawHub
(https://clawhub.ai) and the OpenClaw ecosystem into ~/.config/sayri/skills/.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from . import paths

PULSAR_STORE_BASE = "https://store-os.inled.es"
PULSAR_STORE_API = "https://store-os.inled.es/schema/index.json"
PULSAR_STORE_FALLBACK = "https://pulsar-store.pages.dev/schema/index.json"
CLAWHUB_API_BASE = "https://clawhub.ai/api/v1"
CLAWHUB_RAW_BASE = "https://raw.githubusercontent.com/openclaw/skills/main"


def list_skills() -> list[dict[str, Any]]:
    """List all locally installed skills in ~/.config/sayri/skills/."""
    skills_root = paths.skills_dir()
    if not os.path.isdir(skills_root):
        return []

    result = []
    for entry in sorted(os.listdir(skills_root)):
        entry_path = os.path.join(skills_root, entry)
        if not os.path.isdir(entry_path):
            continue
        skill_file = os.path.join(entry_path, "SKILL.md")
        desc = ""
        if os.path.isfile(skill_file):
            try:
                with open(skill_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line_s = line.strip()
                        if line_s.startswith("description:"):
                            desc = line_s.split("description:", 1)[1].strip(" \"'")
                            break
                        elif line_s and not line_s.startswith(("#", "---", "name:")) and not desc:
                            desc = line_s
            except Exception:
                pass
        result.append({
            "name": entry,
            "path": entry_path,
            "skill_file": skill_file if os.path.isfile(skill_file) else None,
            "description": desc or "Installed skill",
        })
    return result


def read_skill(name: str) -> Optional[str]:
    """Read the full SKILL.md of an installed skill."""
    skill_file = os.path.join(paths.skills_dir(), name, "SKILL.md")
    if os.path.isfile(skill_file):
        try:
            with open(skill_file, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as exc:
            return f"Error reading skill {name}: {exc}"
    return None


def fetch_store_catalog() -> list[dict[str, Any]]:
    """Fetches the official Pulsar Store catalog."""
    for url in [PULSAR_STORE_API, PULSAR_STORE_FALLBACK]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Sayri-Pulsar/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("packages", [])
        except Exception as exc:
            pass
    return []


def search_clawhub(query: str) -> list[dict[str, Any]]:
    """Search ClawHub registry (https://clawhub.ai) for skills."""
    url = f"{CLAWHUB_API_BASE}/search?q={urllib.parse.quote(query)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Sayri-Pulsar/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", data.get("skills", []))
            out = []
            for r in results:
                out.append({
                    "slug": r.get("slug") or r.get("name"),
                    "name": r.get("displayName") or r.get("name") or r.get("slug"),
                    "owner": r.get("ownerHandle") or (r.get("owner") or {}).get("handle", "clawhub"),
                    "description": r.get("summary") or r.get("description") or "",
                    "downloads": r.get("downloads", 0),
                    "source": "ClawHub"
                })
            return out
    except Exception:
        return []


def search_skills(query: str) -> list[dict[str, Any]]:
    """Search both Pulsar Store (https://store-os.inled.es) and ClawHub, prioritizing official store packages."""
    official_results = []
    community_results = []
    seen = set()

    # 1. Pulsar Store Official (Highest Priority)
    packages = fetch_store_catalog()
    q = query.strip().lower()
    for p in packages:
        if p.get("type") in ["sayri_skill", "sayri_plugin"]:
            name = p.get("name", "").lower()
            pkg_id = p.get("id", "").lower()
            desc = p.get("description", "").lower()
            if not q or q in name or q in pkg_id or q in desc:
                slug = p.get("id")
                seen.add(slug)
                official_results.append({
                    "slug": slug,
                    "name": p.get("name"),
                    "owner": p.get("author", "Pulsar OS"),
                    "description": p.get("description", ""),
                    "download_url": p.get("download_url"),
                    "version": p.get("version", "1.0.0"),
                    "is_official": True,
                    "source": "OFFICIAL (Pulsar Store)"
                })

    # 2. ClawHub Registry (Secondary Community Fallback)
    if q:
        for r in search_clawhub(query):
            if r["slug"] not in seen:
                seen.add(r["slug"])
                r["is_official"] = False
                r["source"] = "Community (ClawHub)"
                community_results.append(r)

    # Official store packages always come first
    return official_results + community_results


def _safe_extract_zip(zip_bytes: bytes, target_dir: str) -> bool:
    """Extracts zip archive while strictly preventing Zip Slip path traversal."""
    import io
    import zipfile
    from pathlib import Path

    target_path = Path(target_dir).resolve()
    target_path.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for member in z.infolist():
                # Disallow absolute paths or parent directory traversal
                member_path = (target_path / member.filename).resolve()
                if not str(member_path).startswith(str(target_path)):
                    print(f"[Skills Security] 🚨 Blocked Zip Slip path traversal attempt: {member.filename}", file=sys.stderr)
                    return False

            for member in z.infolist():
                z.extract(member, str(target_path))
        return True
    except Exception as exc:
        print(f"[Skills Security] Zip extraction error: {exc}", file=sys.stderr)
        return False


def install_skill(slug_or_name: str) -> bool:
    """Download and extract official skill package from Pulsar Store or ClawHub into ~/.config/sayri/skills/<name>/."""
    import re

    slug_raw = slug_or_name.strip().lstrip("@")
    owner = None
    slug = slug_raw
    if "/" in slug_raw:
        owner, slug = slug_raw.split("/", 1)

    # Strictly sanitize slug to prevent path traversal
    slug = re.sub(r"[^\w\-]", "", slug).strip("_")
    if not slug:
        return False

    target_dir = os.path.join(paths.skills_dir(), slug)
    os.makedirs(target_dir, exist_ok=True)

    # 1. First priority: Search Pulsar Store catalog
    packages = fetch_store_catalog()
    target_pkg = next((p for p in packages if p.get("id") == slug or p.get("name", "").lower() == slug.lower()), None)

    if target_pkg and target_pkg.get("download_url"):
        dl_url = target_pkg["download_url"]
        print(f"[Skills] 🌐 Fetching from Pulsar Store: {dl_url}")
        try:
            req = urllib.request.Request(dl_url, headers={"User-Agent": "Sayri-Pulsar/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read()
                if content.startswith(b"PK\x03\x04"):
                    if _safe_extract_zip(content, target_dir):
                        print(f"[Skills] ✓ Safely extracted Pulsar Store skill '{slug}' into {target_dir}")
                        return True
        except Exception as exc:
            print(f"[Skills] Pulsar Store download notice: {exc}")

    # 2. Second priority: Download from ClawHub API
    dl_url = f"{CLAWHUB_API_BASE}/download?slug={slug}" + (f"&ownerHandle={owner}" if owner else "")
    print(f"[Skills] 🌐 Fetching from ClawHub: {dl_url}")
    try:
        req = urllib.request.Request(dl_url, headers={"User-Agent": "Sayri-Pulsar/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
            if content.startswith(b"PK\x03\x04") or b"SKILL.md" in content:
                if _safe_extract_zip(content, target_dir):
                    print(f"[Skills] ✓ Safely extracted ClawHub skill '{slug}' into {target_dir}")
                    return True
    except Exception as exc:
        print(f"[Skills] ClawHub download notice: {exc}")

    # 3. Third priority: Fallback to GitHub OpenClaw skills repo
    github_urls = [
        f"{CLAWHUB_RAW_BASE}/skills/{slug}/SKILL.md",
        f"{CLAWHUB_RAW_BASE}/{slug}/SKILL.md",
    ]
    for g_url in github_urls:
        try:
            req = urllib.request.Request(g_url, headers={"User-Agent": "Sayri-Pulsar/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read().decode("utf-8")
                if data and "404" not in data[:40]:
                    target_file = os.path.join(target_dir, "SKILL.md")
                    with open(target_file, "w", encoding="utf-8") as f:
                        f.write(data)
                    print(f"[Skills] ✓ Downloaded '{slug}' from GitHub repository: {g_url}")
                    return True
        except Exception:
            continue

    return False

    # 3. If neither available, create local template
    target_file = os.path.join(target_dir, "SKILL.md")
    if not os.path.isfile(target_file):
        template = f"""---
name: {slug}
description: Skill for {slug} in Sayri / Pulsar OS
---

# Skill: {slug}

## Instructions
When the user asks for tasks related to {slug}, use appropriate bash commands.
"""
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(template)
        print(f"[Skills] ✓ Created local skill template for '{slug}' at {target_file}")

    # 4. Security Pre-Flight Audit
    try:
        from sayri.domain.skills_scanner import SkillsScanner
        report = SkillsScanner.audit_skill_file(target_file)
        print(f"[Skills] 🔍 Security Audit Report for '{slug}': Risk {report.risk_score}/100, Recommendation: {report.recommendation}")
        if report.recommendation == "BLOCK":
            print(f"[Skills] 🛑 BLOCKED: Skill '{slug}' failed security audit with severe risks: {report.warnings}")
            shutil.rmtree(target_dir, ignore_errors=True)
            return False
        elif report.warnings:
            for w in report.warnings:
                print(f"[Skills] {w}")
    except Exception as exc:
        print(f"[Skills] Security scanner warning: {exc}")

    return True


def uninstall_skill(slug_or_name: str) -> bool:
    """Remove an installed skill or plugin from ~/.config/sayri/skills/<name>/."""
    slug = slug_or_name.strip()
    target_dir = os.path.join(paths.skills_dir(), slug)
    plugin_dir = os.path.join(paths.config_dir(), "plugins", slug)

    removed = False
    for d in [target_dir, plugin_dir]:
        if os.path.isdir(d):
            try:
                shutil.rmtree(d)
                print(f"[Skills] ✓ Removed '{slug}' from {d}")
                removed = True
            except Exception as exc:
                print(f"[Skills] Error removing {d}: {exc}")

    if not removed:
        print(f"[Skills] Skill or plugin '{slug}' is not currently installed.")
        return False
    return True


def remove_skill(slug_or_name: str) -> bool:
    """Alias for uninstall_skill."""
    return uninstall_skill(slug_or_name)


def main() -> int:
    """CLI tool for sayri-skills and sayri-plugins."""
    args = sys.argv[1:]
    prog_name = os.path.basename(sys.argv[0])
    if not args or args[0] in ("-h", "--help"):
        print(f"Usage: {prog_name} <command> [args]")
        print("Commands:")
        print("  list                       List installed skills and plugins")
        print("  search <query>             Search Pulsar Store (store-os.inled.es) & ClawHub")
        print("  install <name|id>          Install skill or plugin from Pulsar Store or ClawHub")
        print("  uninstall <name|id>        Remove installed skill or plugin")
        print("  remove <name|id>           Alias for uninstall")
        print("  read <name|id>             Read SKILL.md for an installed skill")
        return 0

    cmd = args[0]
    paths.ensure_dirs()

    if cmd == "list":
        skills = list_skills()
        if not skills:
            print(f"No skills installed in {paths.skills_dir()}")
            return 0
        print(f"\n🤖 Installed Sayri Skills & Plugins ({len(skills)}):\n")
        print(f"{'NAME / ID':<32} | {'DESCRIPTION'}")
        print("-" * 75)
        for s in skills:
            print(f"{s['name']:<32} | {s['description']}")
        return 0

    elif cmd == "read":
        if len(args) < 2:
            print(f"Error: Specify skill name. Usage: {prog_name} read <name>")
            return 1
        content = read_skill(args[1])
        if content:
            print(content)
        else:
            print(f"Skill '{args[1]}' not found.")
            return 1
        return 0

    elif cmd == "search":
        query = " ".join(args[1:]) if len(args) > 1 else ""
        results = search_skills(query)
        print(f"\n🔍 Search results for '{query}' ({len(results)} found):\n")
        print(f"{'STATUS / SOURCE':<28} | {'ID / SLUG':<30} | {'NAME & SUMMARY'}")
        print("=" * 95)
        for r in results:
            if r.get("is_official"):
                src_badge = "\033[1;32m⭐ OFFICIAL (Pulsar)\033[0m"
            else:
                src_badge = "\033[90m🌐 Community (ClawHub)\033[0m"
            slug = r.get('slug', r.get('name'))
            desc = r.get('description', '')[:45].replace('\n', ' ')
            print(f"{src_badge:<37} | {slug:<30} | {r.get('name')} - {desc}")
        print("\n💡 Tip: Official Pulsar Store packages (⭐) are audited with OpenCode & VirusTotal for Pulsar OS.")
        print(f"👉 Run '{prog_name} install <id>' to install any skill or plugin.")
        return 0

    elif cmd == "install":
        if len(args) < 2:
            print(f"Error: Specify skill name. Usage: {prog_name} install <name>")
            return 1
        name = args[1]
        ok = install_skill(name)
        if ok:
            print(f"🎉 Skill/Plugin '{name}' is ready in {os.path.join(paths.skills_dir(), name)}")
        return 0 if ok else 1

    elif cmd in ("uninstall", "remove"):
        if len(args) < 2:
            print(f"Error: Specify skill name. Usage: {prog_name} {cmd} <name>")
            return 1
        name = args[1]
        ok = uninstall_skill(name)
        return 0 if ok else 1

    else:
        print(f"Unknown command: {cmd}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
