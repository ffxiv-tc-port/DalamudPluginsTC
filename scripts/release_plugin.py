#!/usr/bin/env python3
"""Local release automation: tag + push a plugin repo, wait for its
release.yml to finish, then mirror everything into this repo's own
repo.json/releases.

Replaces the old hourly `mirror-releases.yml` GitHub Actions cron (removed
2026-07-17) - that workflow burned an Actions run every hour even when
nothing changed. Since every actual release is cut by hand from this
machine anyway, running the sync locally right after is both free and
faster (no waiting for the next cron tick).

Usage:
    python3 scripts/release_plugin.py --all
    python3 scripts/release_plugin.py EurekaHelper Accountant
    python3 scripts/release_plugin.py --all --dry-run
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import mirror_releases as mirror

GH = mirror.GH

# InternalName -> local checkout path (relative to this repo's parent, D:\)
LOCAL_PATHS = {
    "AutoHook": r"D:\AutoHook",
    "EurekaHelper": r"D:\EurekaHelper",
    "Accountant": r"D:\Accountant",
    "AutoRetainer": r"D:\AutoRetainer",
    "Saucy": r"D:\Saucy",
    "LogogramHelper": r"D:\LogogramHelper",
    "TriadBuddy": r"D:\FFTriadBuddyDalamud",
    "SomethingNeedDoing": r"D:\SomethingNeedDoing",
    "BossModReborn": r"D:\BossmodReborn",
    "WrathCombo": r"D:\WrathCombo",
    "LatihasChocobo": r"D:\LatihasChocobo",
    "Artisan": r"D:\Artisan",
    "Splatoon": r"D:\Splatoon",
    "vnavmesh": r"D:\vnavmesh",
    "InventoryTools": r"D:\InventoryTools",
    "Lifestream": r"D:\Lifestream",
    "visland": r"D:\visland",
    "SubmarineTracker": r"D:\SubmarineTracker",
    "YesAlready": r"D:\YesAlready",
    "GatherbuddyReborn": r"D:\GatherBuddyReborn",
    "ItemVendorLocation": r"D:\ItemVendorLocation",
    "CharacterPanelRefined": r"D:\CharacterPanelRefined",
    "HuntHelper": r"D:\HuntHelper",
    "LazyLoot": r"D:\LazyLoot",
    "MiniMappingway": r"D:\MiniMappingway",
    "NecroLens": r"D:\NecroLens",
    "NotificationMaster": r"D:\NotificationMaster",
    "PalacePal": r"D:\PalacePal",
    "PixelPerfect": r"D:\PixelPerfect",
    "PriceInsight": r"D:\PriceInsight",
    "QoLBar": r"D:\QoLBar",
    "SonarPlugin": r"D:\SonarPlugin",
    "AvantGarde": r"D:\AvantGarde",
    "Dynamis": r"D:\Dynamis",
    "ChatTwo": r"D:\ChatTwo",
    "XivTreasureParty": r"D:\XivTreasureParty",
}

BRANCH = "tc-7.15"
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)\.(\d+)$")


def git(repo_path, *args, check=True):
    result = subprocess.run(["git", "-C", str(repo_path), *args],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise RuntimeError(f"git -C {repo_path} {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def next_tag(internal_name, latest_tag):
    if latest_tag is None:
        return "v7.15.0.1"
    m = VERSION_RE.match(latest_tag)
    if not m:
        raise RuntimeError(f"{internal_name}: latest tag {latest_tag!r} doesn't match "
                            f"the vMAJOR.MINOR.PATCH.BUILD scheme, pick the next tag by hand")
    major, minor, patch, build = m.groups()
    return f"v{major}.{minor}.{patch}.{int(build) + 1}"


def has_uncommitted_changes(repo_path):
    return bool(git(repo_path, "status", "--short"))


def already_released(repo_path, tag):
    """True if the given tag already exists on origin and points at the
    same commit as the local branch tip - i.e. HEAD has no new work since
    that release, so cutting another tag would be a no-op duplicate."""
    remote_sha = git(repo_path, "ls-remote", "origin", f"refs/tags/{tag}", check=False)
    if not remote_sha:
        return False
    remote_sha = remote_sha.split()[0]
    head_sha = git(repo_path, "rev-parse", "HEAD")
    return remote_sha == head_sha


def wait_for_release_run(source_repo, tag, timeout_s=300, poll_s=8):
    """Poll for the release.yml run triggered by this tag and block until it
    finishes. Returns (status, conclusion).

    release.yml triggers on `push: tags: - 'v*'`, so the run's headBranch is
    the tag name itself (e.g. "v7.15.0.50"), not the BRANCH constant - match
    against the tag we just pushed, not the working branch."""
    deadline = time.time() + timeout_s
    run_id = None
    while time.time() < deadline:
        out = subprocess.run(
            [GH, "run", "list", "--repo", source_repo, "--workflow=release.yml",
             "--limit", "5", "--json", "databaseId,headBranch,event,status,conclusion,displayTitle"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if out.returncode == 0 and (out.stdout or "").strip():
            runs = json.loads(out.stdout)
            for r in runs:
                if r["event"] == "push" and r["headBranch"] == tag:
                    run_id = r["databaseId"]
                    if r["status"] == "completed":
                        return r["status"], r["conclusion"]
                    break
        time.sleep(poll_s)
    return ("timeout", None) if run_id is None else ("timeout", "unknown")


def release_one(internal_name, source_repo, dry_run=False):
    repo_path = LOCAL_PATHS.get(internal_name)
    if repo_path is None:
        print(f"[skip] {internal_name}: no LOCAL_PATHS entry")
        return False
    if not Path(repo_path).exists():
        print(f"[skip] {internal_name}: {repo_path} does not exist")
        return False

    if has_uncommitted_changes(repo_path):
        print(f"[skip] {internal_name}: uncommitted changes in {repo_path}, "
              f"commit or stash first")
        return False

    rel = mirror.latest_release(source_repo)
    latest_tag = rel["tag"] if rel else None

    if latest_tag is not None and already_released(repo_path, latest_tag):
        print(f"[skip] {internal_name}: HEAD already released as {latest_tag}, nothing new to tag")
        return True

    tag = next_tag(internal_name, latest_tag)
    print(f"[{internal_name}] next tag: {tag}")
    if dry_run:
        return True

    git(repo_path, "tag", tag)
    git(repo_path, "push", "origin", tag)
    print(f"[{internal_name}] pushed {tag}, waiting for release.yml...")

    status, conclusion = wait_for_release_run(source_repo, tag)
    if status != "completed" or conclusion != "success":
        print(f"[FAIL] {internal_name}: release.yml {status}/{conclusion} — "
              f"check https://github.com/{source_repo}/actions")
        return False

    print(f"[ok] {internal_name}: {tag} released")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugins", nargs="*", help="InternalNames to release (default: none unless --all)")
    parser.add_argument("--all", action="store_true", help="Release every plugin in SOURCE_REPOS")
    parser.add_argument("--dry-run", action="store_true", help="Only print the tag that would be cut")
    parser.add_argument("--skip-mirror", action="store_true", help="Don't run mirror_releases.py afterward")
    parser.add_argument("--workers", type=int, default=8,
                         help="How many plugins to release in parallel (default: 8)")
    args = parser.parse_args()

    if args.all:
        targets = list(mirror.SOURCE_REPOS.keys())
    else:
        targets = args.plugins
        unknown = [t for t in targets if t not in mirror.SOURCE_REPOS]
        if unknown:
            sys.exit(f"Unknown plugin(s): {unknown}. Known: {list(mirror.SOURCE_REPOS.keys())}")

    if not targets:
        sys.exit("Nothing to do — pass plugin names or --all")

    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(release_one, name, mirror.SOURCE_REPOS[name], dry_run=args.dry_run): name
            for name in targets
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                print(f"[FAIL] {name}: {exc}")
                results[name] = False

    ok = [n for n, v in results.items() if v]
    failed = [n for n, v in results.items() if not v]
    print(f"\n{len(ok)} succeeded, {len(failed)} failed/skipped: {failed}")

    if args.dry_run or args.skip_mirror or not ok:
        return

    print("\nRunning mirror_releases.py to sync repo.json...")
    mirror.main()


if __name__ == "__main__":
    main()
