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

FLEET_ROOT = Path(r"D:\ffxiv-tc-port")

# InternalNames that do NOT have a checkout of their own because they ship from
# another plugin's repo and tag. Releasing these directly is meaningless - they
# come along for free when their host repo is released.
#   DynamisWithSMA: second asset pair produced by Dynamis's own release.yml.
ALIAS_INTERNAL_NAMES = {"DynamisWithSMA"}

# InternalName -> local checkout, DERIVED from mirror.SOURCE_REPOS rather than
# hand-listed. Verified 2026-07-29 against the previous hand-written table:
# all 52 real checkouts are exactly FLEET_ROOT / <repo name>, zero exceptions -
# including the ones that look like exceptions (TriadBuddy ->
# FFTriadBuddyDalamud, BossModReborn -> BossmodReborn, GatherbuddyReborn ->
# GatherBuddyReborn), because the repo name already encodes the difference.
#
# This used to be a third registration point next to SOURCE_REPOS and
# ICON_PATHS, and on 2026-07-29 forgetting it made all 8 newly-onboarded
# plugins silently `[skip] no LOCAL_PATHS entry` on release. Deriving it
# removes that failure mode entirely.
LOCAL_PATHS = {
    name: str(FLEET_ROOT / src.split("/", 1)[1])
    for name, src in mirror.SOURCE_REPOS.items()
    if name not in ALIAS_INTERNAL_NAMES
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


def _poll_release_run(source_repo, tag):
    """One lookup of the dispatched run. Returns (found, status, conclusion)."""
    out = subprocess.run(
        [GH, "run", "list", "--repo", source_repo, "--workflow=release.yml",
         "--limit", "5", "--json", "databaseId,headBranch,event,status,conclusion,displayTitle"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0 or not (out.stdout or "").strip():
        return False, None, None
    for r in json.loads(out.stdout):
        if r["event"] == "workflow_dispatch" and r["headBranch"] == tag:
            return True, r["status"], r["conclusion"]
    return False, None, None


def wait_for_release_run(source_repo, tag, timeout_s=900, poll_s=8):
    """Poll for the release.yml run triggered by dispatch_release_run() and
    block until it finishes. Returns (status, conclusion).

    release.yml's only trigger is workflow_dispatch (see dispatch_release_run),
    dispatched against the tag ref - so the run's headBranch is the tag name
    itself (e.g. "v7.15.0.50"), not the BRANCH constant.

    timeout_s was 300 until 2026-07-29, which was SHORTER THAN REAL BUILDS and
    the live source of this script's documented history of false negatives:
    measured release.yml durations at the time were BossModReborn 387s and
    Artisan 343s, both of which reported [FAIL] on releases that had actually
    succeeded. Raised to 900s, and the deadline is now followed by one final
    lookup so a run that completes in the last poll interval isn't misreported."""
    deadline = time.time() + timeout_s
    seen = False
    while time.time() < deadline:
        found, status, conclusion = _poll_release_run(source_repo, tag)
        seen = seen or found
        if found and status == "completed":
            return status, conclusion
        time.sleep(poll_s)

    # One last look - the loop can exit with a run that finished during the
    # final sleep, and reporting FAIL for a successful release is worse than
    # waiting one more round-trip.
    found, status, conclusion = _poll_release_run(source_repo, tag)
    if found and status == "completed":
        return status, conclusion
    return ("timeout", None) if not (seen or found) else ("timeout", "unknown")


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
        # Exclude alias InternalNames: they have no checkout of their own, so
        # including them made every single --all run report a phantom failure
        # ("[skip] DynamisWithSMA: no LOCAL_PATHS entry"). A run that always
        # ends with "1 failed" trains you to ignore the failure count, which is
        # worse than the non-problem it was reporting.
        targets = [n for n in mirror.SOURCE_REPOS if n not in ALIAS_INTERNAL_NAMES]
    else:
        targets = args.plugins
        unknown = [t for t in targets if t not in mirror.SOURCE_REPOS]
        if unknown:
            sys.exit(f"Unknown plugin(s): {unknown}. Known: {list(mirror.SOURCE_REPOS.keys())}")
        aliases = [t for t in targets if t in ALIAS_INTERNAL_NAMES]
        if aliases:
            sys.exit(f"{aliases} ship from another plugin's repo and cannot be released "
                     f"directly - release the host plugin instead (e.g. Dynamis for "
                     f"DynamisWithSMA).")

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

    if args.all:
        scope = None  # full sweep; we touched everything anyway
    else:
        # Scope the mirror to what we actually released - a full sweep is ~156
        # `gh` round-trips regardless of how little changed.
        #
        # Expand by SOURCE REPO, not by name: releasing Dynamis also builds
        # DynamisWithSMA's assets from the same tag, and mirroring only
        # "Dynamis" would leave DynamisWithSMA's feed entry pointing at the
        # previous release.
        released_repos = {mirror.SOURCE_REPOS[n] for n in ok}
        scope = sorted(n for n, r in mirror.SOURCE_REPOS.items() if r in released_repos)

    print("\nRunning mirror_releases.py to sync repo.json...")
    mirror.main(only=scope)


if __name__ == "__main__":
    main()
