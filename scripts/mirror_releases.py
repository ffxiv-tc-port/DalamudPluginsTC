#!/usr/bin/env python3
"""Poll private plugin repos for new releases and mirror their assets into
this public repo's own releases, then refresh repo.json.

Runs only inside the DalamudPluginsTC repo's own GitHub Actions workflow,
using a token that is never stored in the source plugin repos.
"""
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_JSON = REPO_ROOT / "repo.json"
STATE_FILE = REPO_ROOT / "scripts" / "release-state.json"
ICONS_DIR = REPO_ROOT / "icons"
PUBLIC_REPO = "ffxiv-tc-port/DalamudPluginsTC"

# InternalName -> source repo (owner/name)
SOURCE_REPOS = {
    "AutoHook": "ffxiv-tc-port/AutoHook",
    "AutoDuty": "ffxiv-tc-port/AutoDuty",
    "Avarice": "ffxiv-tc-port/Avarice",
    "EurekaHelper": "ffxiv-tc-port/EurekaHelper",
    "Accountant": "ffxiv-tc-port/Accountant",
    "AutoRetainer": "ffxiv-tc-port/AutoRetainer",
    "Saucy": "ffxiv-tc-port/Saucy",
    "LogogramHelper": "ffxiv-tc-port/LogogramHelper",
    "TriadBuddy": "ffxiv-tc-port/FFTriadBuddyDalamud",
    "SomethingNeedDoing": "ffxiv-tc-port/SomethingNeedDoing",
    "BossModReborn": "ffxiv-tc-port/BossmodReborn",
    "WrathCombo": "ffxiv-tc-port/WrathCombo",
    "LatihasChocobo": "ffxiv-tc-port/LatihasChocobo",
    "Artisan": "ffxiv-tc-port/Artisan",
    "Splatoon": "ffxiv-tc-port/Splatoon",
    "vnavmesh": "ffxiv-tc-port/vnavmesh",
    "InventoryTools": "ffxiv-tc-port/InventoryTools",
    "visland": "ffxiv-tc-port/visland",
    "Lifestream": "ffxiv-tc-port/Lifestream",
    "SubmarineTracker": "ffxiv-tc-port/SubmarineTracker",
    "YesAlready": "ffxiv-tc-port/YesAlready",
    "GatherbuddyReborn": "ffxiv-tc-port/GatherBuddyReborn",
    "ItemVendorLocation": "ffxiv-tc-port/ItemVendorLocation",
    "CharacterPanelRefined": "ffxiv-tc-port/CharacterPanelRefined",
    "HuntHelper": "ffxiv-tc-port/HuntHelper",
    "LazyLoot": "ffxiv-tc-port/LazyLoot",
    "MiniMappingway": "ffxiv-tc-port/MiniMappingway",
    "NecroLens": "ffxiv-tc-port/NecroLens",
    "NotificationMaster": "ffxiv-tc-port/NotificationMaster",
    "PalacePal": "ffxiv-tc-port/PalacePal",
    "PixelPerfect": "ffxiv-tc-port/PixelPerfect",
    "PriceInsight": "ffxiv-tc-port/PriceInsight",
    "QoLBar": "ffxiv-tc-port/QoLBar",
    "SonarPlugin": "ffxiv-tc-port/SonarPlugin",
    "AvantGarde": "ffxiv-tc-port/AvantGarde",
    "Dynamis": "ffxiv-tc-port/Dynamis",
    # Dynamis's release.yml ships two plugins (Dynamis + the "with hosted
    # PowerShell" variant) from one tag/release - see the asset-matching-by-
    # InternalName logic above for how both get mirrored correctly from a
    # single shared release.
    "DynamisWithSMA": "ffxiv-tc-port/Dynamis",
    "ChatTwo": "ffxiv-tc-port/ChatTwo",
    "XivTreasureParty": "ffxiv-tc-port/XivTreasureParty",
    "SkipCutscene": "ffxiv-tc-port/SkipCutscene",
    "DailyDuty": "ffxiv-tc-port/DailyDuty",
    "Questionable": "ffxiv-tc-port/Questionable",
    "TextAdvance": "ffxiv-tc-port/TextAdvance",
}

# InternalName -> icon path within the source repo (on its default branch).
# raw.githubusercontent.com can't serve files from private repos anonymously,
# so we mirror the icon into this public repo instead.
ICON_PATHS = {
    "EurekaHelper": "EurekaHelper/Resources/icon.png",
    "Accountant": "images/icon.png",
    "AutoRetainer": "AutoRetainer/res/autoretainer.png",
    "Saucy": "Saucy/Icon.png",
    "LogogramHelper": "res/img/logoslogo.png",
    "SomethingNeedDoing": "res/icon.png",
    "BossModReborn": "Data/icon.png",
    "WrathCombo": "res/plugin/wrathcombo.png",
    "LatihasChocobo": "Resources/icon.png",
    "Artisan": "Artisan/Images/Icon.png",
    "Splatoon": "Splatoon/res/icon.png",
    "vnavmesh": "icon2.png",
    "InventoryTools": "InventoryTools/Images/icon.png",
    "visland": "icon.png",
    "Lifestream": "Lifestream/images/icon.png",
    "SubmarineTracker": "SubmarineTracker/images/icon.png",
    "YesAlready": "Assets/yesalready_icon.png",
    "GatherbuddyReborn": "images/icon.png",
    "ItemVendorLocation": "Images/icon.png",
    "CharacterPanelRefined": "CharacterPanelRefined/images/icon.png",
    "HuntHelper": "Images/icon.png",
    "NecroLens": "icon.png",
    "NotificationMaster": "NotificationMaster/images/icon.png",
    "PalacePal": "Assets/palacepal_icon.png",
    "PixelPerfect": "images/icon.png",
    "PriceInsight": "images/icon.png",
    "AvantGarde": "Images/icon.png",
    "Dynamis": "Dynamis/Resources/Dynamis128.png",
    "DynamisWithSMA": "Dynamis/DynamisWithSMA128.png",
    "ChatTwo": "ChatTwo/images/icon.png",
    "XivTreasureParty": "XivTreasureParty/Resources/icon.png",
    "AutoHook": "images/icon.png",
    "TriadBuddy": "assets/icon.png",
    "LazyLoot": "images/icon.png",
    "QoLBar": "images/icon.png",
    "MiniMappingway": "images/icon.png",
    # SonarPlugin: no local icon asset upstream (only an external CDN IconUrl);
    # icons/SonarPlugin.png is manually sourced from assets.ffxivsonar.com/dalamud/logo.png.
    # SkipCutscene: upstream never shipped a plugin icon at all; sourced a
    # thematically-fitting placeholder (FFXIV's "Sprint" action icon) via
    # v2.xivapi.com/api/asset (same technique as LatihasChocobo's icon).
    "SkipCutscene": "SkipCutscene/Resources/icon.png",
    # DailyDuty: upstream never shipped a plugin icon either; sourced the
    # "Journal Stationery Set" item icon via v2.xivapi.com/api/asset.
    "DailyDuty": "DailyDuty/Resources/icon.png",
    "AutoDuty": "logo.png",
    "Avarice": "Assets/avarice_icon.png",
    # Questionable: no local icon asset in the repo, upstream's own manifest points at
    # an external CDN IconUrl (puni.sh); icons/Questionable.png is manually sourced from
    # the actual upstream icon at github.com/qstxiv/icons/raw/main/Questionable.png
    # (converted RGB->RGBA to avoid the broken-icon-question-mark bug).
}


def gh(*args, check=True):
    result = subprocess.run([GH, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def latest_release(repo):
    out = gh("api", f"repos/{repo}/releases", "--jq",
              "sort_by(.published_at) | reverse | .[0] "
              "| {tag: .tag_name, assets: [.assets[] | {name, url}]}",
              check=False)
    if not out or out == "null":
        return None
    return json.loads(out)


def download_asset(asset_url, dest):
    with open(dest, "wb") as f:
        subprocess.run(
            [GH, "api", asset_url, "-H", "Accept: application/octet-stream"],
            stdout=f, check=True,
        )


MAX_CHANGELOG_COMMITS = 25


def get_changelog(source_repo, prev_tag, tag):
    """Build a bullet-list changelog for repo.json's "Changelog" field from
    the commit messages between the previously-mirrored tag and this one.
    These commits are all authored by Claude during release work, so the
    first line of each message is already a reasonable changelog entry -
    no upstream release-notes body needed (those are just GitHub's
    auto-generated compare links anyway, and point at a private repo the
    end user can't open). Returns None if there's no previous tag to diff
    against (first-ever mirror of this plugin) or the API call fails."""
    if not prev_tag:
        return None
    out = gh("api", f"repos/{source_repo}/compare/{prev_tag}...{tag}", "--jq",
              '[.commits[].commit.message | split("\\n")[0]] | reverse | .[]', check=False)
    if not out:
        return None
    lines = [line for line in out.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) > MAX_CHANGELOG_COMMITS:
        lines = lines[:MAX_CHANGELOG_COMMITS] + [f"...and {len(lines) - MAX_CHANGELOG_COMMITS} more commits"]
    return "\n".join(f"- {line}" for line in lines)


_source_archive_cache = {}
_source_archive_locks_guard = threading.Lock()
_source_archive_locks = defaultdict(threading.Lock)
_SOURCE_ARCHIVE_DIR = Path(tempfile.mkdtemp(prefix="mirror-src-"))


def _clear_readonly_and_retry(func, path, exc_info):
    """shutil.rmtree onerror handler. Git pack/idx files are written read-only
    on Windows; the plain rmtree(..., ignore_errors=True) this used to call
    silently swallowed the resulting PermissionError, leaving the ENTIRE .git
    directory (with real commit-author name/email) intact inside the "source"
    zip that got uploaded to a PUBLIC release on 2026-07-25 - caught only by
    manually inspecting a downloaded archive, not by the script itself. Never
    go back to ignore_errors=True for this - clear the read-only bit and
    retry instead, and let genuinely unrecoverable errors propagate."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def build_source_archive(source_repo, tag):
    """Clone the tagged commit (with submodules) from the private source repo,
    strip every .git directory (top-level and every submodule's own - these
    hold real commit-author identity, not just history noise), and zip the
    result. Cached per (source_repo, tag) since Dynamis/DynamisWithSMA share
    one source repo/tag across two InternalNames - the cache and its backing
    files must outlive any single per-release TemporaryDirectory, so this
    uses its own directory (_SOURCE_ARCHIVE_DIR) that lives for the whole
    script run, not the caller's per-iteration `tmp`. Thread-safe: callers
    for different (source_repo, tag) keys run fully in parallel; callers for
    the SAME key (only Dynamis/DynamisWithSMA today) serialize on that key's
    own lock rather than blocking every other repo.

    Returns the path to the created zip. Raises if the clone or the .git
    strip fails - a source archive that silently fails to build (or worse,
    silently ships live commit history) is worse than the whole mirror run
    failing loudly."""
    key = (source_repo, tag)
    with _source_archive_locks_guard:
        lock = _source_archive_locks[key]
    with lock:
        if key in _source_archive_cache:
            return _source_archive_cache[key]

        workdir = _SOURCE_ARCHIVE_DIR / f"{source_repo.replace('/', '_')}-{tag}"
        workdir.mkdir(parents=True, exist_ok=True)
        clone_dir = workdir / "src-clone"
        result = subprocess.run(
            [GH, "repo", "clone", source_repo, str(clone_dir), "--",
             "--recurse-submodules", "--depth", "1", "--branch", tag],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(f"source archive: clone of {source_repo}@{tag} failed:\n{result.stderr}")

        for git_path in clone_dir.rglob(".git"):
            if git_path.is_dir():
                shutil.rmtree(git_path, onerror=_clear_readonly_and_retry)
            elif git_path.is_file():
                git_path.unlink()

        leftover = list(clone_dir.rglob(".git"))
        if leftover:
            raise RuntimeError(
                f"source archive: {len(leftover)} .git path(s) survived stripping for "
                f"{source_repo}@{tag} - refusing to build an archive that might leak "
                f"commit history: {leftover[:5]}"
            )

        # Not a leak risk like .git, but not "source" either - trim repo/CI/
        # git-plumbing meta-files so the archive is just the buildable
        # program (top-level and every submodule's own). .gitmodules in
        # particular would otherwise describe every submodule as an
        # external reference to fetch separately, which defeats the point
        # of physically bundling the submodules' own source inline.
        for pattern in (".github", ".gitignore", ".gitattributes", ".gitmodules"):
            for meta_path in clone_dir.rglob(pattern):
                if meta_path.is_dir():
                    shutil.rmtree(meta_path, onerror=_clear_readonly_and_retry)
                elif meta_path.is_file():
                    meta_path.unlink()

        zip_base = workdir / f"source-{source_repo.replace('/', '_')}-{tag}"
        archive_path = Path(shutil.make_archive(str(zip_base), "zip", str(clone_dir)))
        _source_archive_cache[key] = archive_path
        return archive_path


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def sync_icon(internal_name, source_repo, entry):
    icon_path = ICON_PATHS.get(internal_name)
    if not icon_path:
        return False
    out = gh("api", f"repos/{source_repo}/contents/{icon_path}", "--jq", ".content", check=False)
    if not out:
        print(f"[warn] {internal_name}: could not fetch icon at {icon_path}")
        return False
    ICONS_DIR.mkdir(exist_ok=True)
    dest = ICONS_DIR / f"{internal_name}.png"
    data = base64.b64decode(out)
    if dest.exists() and dest.read_bytes() == data:
        return False
    dest.write_bytes(data)
    entry["IconUrl"] = f"https://raw.githubusercontent.com/{PUBLIC_REPO}/main/icons/{internal_name}.png"
    return True


_persist_lock = threading.Lock()


def mirror_one(internal_name, source_repo, state, by_internal):
    """Mirror one plugin's latest release (binary + source archive) into the
    public repo and update its repo.json entry in place. Returns True if
    this repo's state actually changed (new release mirrored or asset
    fixed up), False for a no-op (up to date / no releases yet)."""
    rel = latest_release(source_repo)
    if rel is None:
        print(f"[skip] {internal_name}: no releases found on {source_repo}")
        return False

    tag = rel["tag"]
    public_tag = f"{internal_name}-{tag}"
    prev_tag = state.get(internal_name)
    already_current = prev_tag == tag

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        local_files = []
        manifest = None
        own_assets = [a for a in rel["assets"] if Path(a["name"]).stem == internal_name]
        assets_to_fetch = own_assets if own_assets else rel["assets"]

        existing = gh("release", "view", public_tag, "--repo", PUBLIC_REPO,
                       "--json", "assets", "--jq", "[.assets[].name]", check=False)
        release_exists = existing != ""
        existing_asset_names = json.loads(existing) if existing else []
        has_source_asset = f"{internal_name}-source.zip" in existing_asset_names

        if already_current and release_exists and has_source_asset:
            print(f"[up to date] {internal_name}: {tag}")
            return False

        print(f"[new release] {internal_name}: {tag}" if not already_current
              else f"[fixing up] {internal_name}: {tag} (missing source asset)")

        for asset in assets_to_fetch:
            dest = tmp / asset["name"]
            download_asset(asset["url"], dest)
            local_files.append(dest)
            if asset["name"].endswith(".json") and (not own_assets or Path(asset["name"]).stem == internal_name):
                manifest = json.loads(dest.read_text(encoding="utf-8"))

        # Bundle a matching source snapshot alongside the binary, uniformly for
        # every plugin regardless of upstream license - see the 2026-07-25
        # license-audit decision (memory: project_license_audit_20260725).
        # .git is stripped (top-level + every submodule's own) so this can't
        # leak real commit-author identity, only the "Lother" alias already
        # public via every RepoUrl in repo.json.
        src_zip = build_source_archive(source_repo, tag)
        named_src_zip = tmp / f"{internal_name}-source.zip"
        if src_zip != named_src_zip:
            shutil.copyfile(src_zip, named_src_zip)

        if not release_exists:
            gh("release", "create", public_tag,
               *[str(f) for f in local_files], str(named_src_zip),
               "--repo", PUBLIC_REPO,
               "--title", f"{internal_name} {tag}",
               "--notes", f"Mirrored from {source_repo}@{tag}")
        elif not has_source_asset:
            gh("release", "upload", public_tag, str(named_src_zip),
               "--repo", PUBLIC_REPO, "--clobber")
            print(f"[uploaded source asset] {internal_name}: {public_tag}")
        else:
            print(f"[already mirrored] {internal_name}: {public_tag} exists with source asset, "
                  f"only updating repo.json below")

        entry = by_internal.get(internal_name)
        if entry is None:
            print(f"[warn] {internal_name} not present in repo.json, skipping metadata update")
            state[internal_name] = tag
            return True

        if manifest:
            for key in ("AssemblyVersion", "Description", "Punchline", "Author"):
                if key in manifest:
                    entry[key] = manifest[key]
        else:
            # No manifest asset published; fall back to parsing the tag itself
            # (e.g. "v7.15.0.47" or "7.15.0.5-cn" -> "7.15.0.47" / "7.15.0.5").
            m = re.search(r"\d+\.\d+\.\d+\.\d+", tag)
            if m:
                entry["AssemblyVersion"] = m.group(0)
        zip_asset = next(
            (a["name"] for a in assets_to_fetch if a["name"].endswith(".zip")), None
        )
        if zip_asset:
            url = f"https://github.com/{PUBLIC_REPO}/releases/download/{public_tag}/{zip_asset}"
            entry["DownloadLinkInstall"] = url
            entry["DownloadLinkUpdate"] = url

        changelog = get_changelog(source_repo, prev_tag, tag)
        if changelog:
            entry["Changelog"] = changelog

        state[internal_name] = tag
        return True


def main():
    state = load_json(STATE_FILE, {})
    repo_json = load_json(REPO_JSON, [])
    by_internal = {e["InternalName"]: e for e in repo_json}

    changed = False

    for internal_name, source_repo in SOURCE_REPOS.items():
        entry = by_internal.get(internal_name)
        if entry is not None and sync_icon(internal_name, source_repo, entry):
            print(f"[icon updated] {internal_name}")
            changed = True

    def run_and_persist(internal_name, source_repo):
        result = mirror_one(internal_name, source_repo, state, by_internal)
        # state/repo_json/by_internal are shared across threads - only the
        # persistence (and the entry mutations inside mirror_one, which all
        # happen before this point in the same call) need serializing.
        with _persist_lock:
            STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            if result:
                REPO_JSON.write_text(json.dumps(repo_json, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return result

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(run_and_persist, name, repo): name
            for name, repo in SOURCE_REPOS.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                if future.result():
                    changed = True
            except Exception as exc:
                print(f"[FAIL] {name}: {exc}")

    shutil.rmtree(_SOURCE_ARCHIVE_DIR, ignore_errors=True)
    print("done" if changed else "no repo.json changes")


if __name__ == "__main__":
    main()
