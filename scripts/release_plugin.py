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
    "ChilledLeves": r"D:\ffxiv-tc-port\ChilledLeves",
    "ICE": r"D:\ffxiv-tc-port\ICE",
    "Deliveroo": r"D:\ffxiv-tc-port\Deliveroo",
    "Gearsetter": r"D:\ffxiv-tc-port\Gearsetter",
    "vfaux": r"D:\ffxiv-tc-port\vfaux",
    "Marketbuddy": r"D:\ffxiv-tc-port\Marketbuddy",
    "JobBars": r"D:\ffxiv-tc-port\JobBars",
    "BOCCHI": r"D:\ffxiv-tc-port\BOCCHI",
    "AutoHook": r"D:\ffxiv-tc-port\AutoHook",
    "AutoDuty": r"D:\ffxiv-tc-port\AutoDuty",
    "Avarice": r"D:\ffxiv-tc-port\Avarice",
    "EurekaHelper": r"D:\ffxiv-tc-port\EurekaHelper",
    "Accountant": r"D:\ffxiv-tc-port\Accountant",
    "AutoRetainer": r"D:\ffxiv-tc-port\AutoRetainer",
    "Saucy": r"D:\ffxiv-tc-port\Saucy",
    "LogogramHelper": r"D:\ffxiv-tc-port\LogogramHelper",
    "TriadBuddy": r"D:\ffxiv-tc-port\FFTriadBuddyDalamud",
    "SomethingNeedDoing": r"D:\ffxiv-tc-port\SomethingNeedDoing",
    "BossModReborn": r"D:\ffxiv-tc-port\BossmodReborn",
    "WrathCombo": r"D:\ffxiv-tc-port\WrathCombo",
    "LatihasChocobo": r"D:\ffxiv-tc-port\LatihasChocobo",
    "Artisan": r"D:\ffxiv-tc-port\Artisan",
    "Splatoon": r"D:\ffxiv-tc-port\Splatoon",
    "vnavmesh": r"D:\ffxiv-tc-port\vnavmesh",
    "InventoryTools": r"D:\ffxiv-tc-port\InventoryTools",
    "Lifestream": r"D:\ffxiv-tc-port\Lifestream",
    "visland": r"D:\ffxiv-tc-port\visland",
    "SubmarineTracker": r"D:\ffxiv-tc-port\SubmarineTracker",
    "YesAlready": r"D:\ffxiv-tc-port\YesAlready",
    "GatherbuddyReborn": r"D:\ffxiv-tc-port\GatherBuddyReborn",
    "ItemVendorLocation": r"D:\ffxiv-tc-port\ItemVendorLocation",
    "CharacterPanelRefined": r"D:\ffxiv-tc-port\CharacterPanelRefined",
    "HuntHelper": r"D:\ffxiv-tc-port\HuntHelper",
    "LazyLoot": r"D:\ffxiv-tc-port\LazyLoot",
    "MiniMappingway": r"D:\ffxiv-tc-port\MiniMappingway",
    "NecroLens": r"D:\ffxiv-tc-port\NecroLens",
    "NotificationMaster": r"D:\ffxiv-tc-port\NotificationMaster",
    "PalacePal": r"D:\ffxiv-tc-port\PalacePal",
    "PixelPerfect": r"D:\ffxiv-tc-port\PixelPerfect",
    "PriceInsight": r"D:\ffxiv-tc-port\PriceInsight",
    "QoLBar": r"D:\ffxiv-tc-port\QoLBar",
    "SonarPlugin": r"D:\ffxiv-tc-port\SonarPlugin",
    "AvantGarde": r"D:\ffxiv-tc-port\AvantGarde",
    "Dynamis": r"D:\ffxiv-tc-port\Dynamis",
    "ChatTwo": r"D:\ffxiv-tc-port\ChatTwo",
    "XivTreasureParty": r"D:\ffxiv-tc-port\XivTreasureParty",
    "SkipCutscene": r"D:\ffxiv-tc-port\SkipCutscene",
    "DailyDuty": r"D:\ffxiv-tc-port\DailyDuty",
    "Questionable": r"D:\ffxiv-tc-port\Questionable",
    "TextAdvance": r"D:\ffxiv-tc-port\TextAdvance",
    "Crossingway": r"D:\ffxiv-tc-port\Crossingway",
    "IINACT": r"D:\ffxiv-tc-port\IINACT",
}

BRANCH = "tc-7.20"
# TC 7.20 era floor: any repo whose latest tag predates this jumps straight
# to v7.20.0.1 instead of incrementing its old 7.15-era build number.
ERA_FLOOR = (7, 20)
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)\.(\d+)$")


def git(repo_path, *args, check=True):
    result = subprocess.run(["git", "-C", str(repo_path), *args],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise RuntimeError(f"git -C {repo_path} {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def next_tag(internal_name, latest_tag):
    if latest_tag is None:
        return "v7.20.0.1"
    m = VERSION_RE.match(latest_tag)
    if not m:
        raise RuntimeError(f"{internal_name}: latest tag {latest_tag!r} doesn't match "
                            f"the vMAJOR.MINOR.PATCH.BUILD scheme, pick the next tag by hand")
    major, minor, patch, build = (int(x) for x in m.groups())
    if (major, minor) < ERA_FLOOR:
        return f"v{ERA_FLOOR[0]}.{ERA_FLOOR[1]}.0.1"
    return f"v{major}.{minor}.{patch}.{build + 1}"


def has_uncommitted_changes(repo_path):
    return bool(git(repo_path, "status", "--short"))


def latest_git_tag(repo_path):
    """Latest vMAJOR.MINOR.PATCH.BUILD tag reachable from HEAD, by version
    sort. Derived from git tags rather than GitHub Releases so it stays
    correct for a freshly-migrated repo that has every tag but no Release
    objects yet (the ffxiv-tc-port org migration left the private repos in
    exactly that state). `--merged HEAD` excludes upstream tags sitting on
    commits that aren't ancestors of our branch (e.g. AutoHook's upstream
    v6.x), so those can't be mistaken for our latest release."""
    out = git(repo_path, "tag", "--merged", "HEAD")
    versions = []
    for line in out.splitlines():
        line = line.strip()
        # Our release tags are ALWAYS v-prefixed (v7.15.x.y). Skipping the
        # bare-numeric ones excludes stray legacy/upstream tags that would
        # otherwise sort higher than ours - e.g. LatihasChocobo carries
        # "7.50.0.1" (no v) which is > v7.15.1.1 under version sort.
        if not line.startswith("v"):
            continue
        m = VERSION_RE.match(line)
        if not m:
            continue
        ver = tuple(int(x) for x in m.groups())
        # Only tags from our own era scheme count (major == ERA_FLOOR major,
        # i.e. the TC game-patch number). Upstream repos ship 4-part v-tags
        # with unrelated majors that version-sort above ours and ARE ancestors
        # of HEAD once upstream history is merged in - e.g. Questionable's
        # upstream v15.277.7.0, which --merged doesn't exclude and which once
        # produced a bogus v15.277.7.1 release (2026-07-28).
        if ver[0] != ERA_FLOOR[0]:
            continue
        versions.append((ver, line))
    if not versions:
        return None
    versions.sort()
    return versions[-1][1]


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


def dispatch_release_run(source_repo, tag, retries=5, delay_s=3):
    """Explicitly trigger release.yml via workflow_dispatch against the tag
    just pushed, instead of relying on GitHub's automatic tag-push event
    dispatch. The ffxiv-tc-port org migration (2026-07-26) left many repos
    with unreliable automatic push/tag dispatch - some took 10+ minutes to
    start firing, others never did even after manually clicking "Enable
    Actions" in the web UI for that repo. workflow_dispatch was 100%
    reliable in testing regardless of that state, so release.yml's trigger
    was changed to workflow_dispatch-only and this is now the sole way a
    release build gets started. Retries a few times since the just-pushed
    tag ref can take a moment to be resolvable by the dispatch API."""
    result = None
    for attempt in range(1, retries + 1):
        result = subprocess.run(
            [GH, "workflow", "run", "release.yml", "--repo", source_repo, "--ref", tag],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            return
        if attempt < retries:
            time.sleep(delay_s)
    raise RuntimeError(f"gh workflow run release.yml --repo {source_repo} --ref {tag} "
                        f"failed after {retries} attempts:\n{result.stderr}")


def wait_for_release_run(source_repo, tag, timeout_s=300, poll_s=8):
    """Poll for the release.yml run triggered by dispatch_release_run() and
    block until it finishes. Returns (status, conclusion).

    release.yml's only trigger is workflow_dispatch (see dispatch_release_run),
    dispatched against the tag ref - so the run's headBranch is the tag name
    itself (e.g. "v7.15.0.50"), not the BRANCH constant."""
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
                if r["event"] == "workflow_dispatch" and r["headBranch"] == tag:
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

    latest_tag = latest_git_tag(repo_path)

    if latest_tag is not None and already_released(repo_path, latest_tag):
        print(f"[skip] {internal_name}: HEAD already released as {latest_tag}, nothing new to tag")
        return True

    tag = next_tag(internal_name, latest_tag)
    print(f"[{internal_name}] next tag: {tag}")
    if dry_run:
        return True

    git(repo_path, "tag", tag)
    git(repo_path, "push", "origin", tag)
    dispatch_release_run(source_repo, tag)
    print(f"[{internal_name}] pushed {tag}, dispatched release.yml, waiting for it...")

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
