#!/usr/bin/env python3
"""Poll private plugin repos for new releases and mirror their assets into
this public repo's own releases, then refresh repo.json.

Runs only inside the DalamudPluginsTC repo's own GitHub Actions workflow,
using a token that is never stored in the source plugin repos.
"""
import base64
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_JSON = REPO_ROOT / "repo.json"
STATE_FILE = REPO_ROOT / "scripts" / "release-state.json"
ICONS_DIR = REPO_ROOT / "icons"
PUBLIC_REPO = "Lother/DalamudPluginsTC"

# InternalName -> source repo (owner/name)
SOURCE_REPOS = {
    "EurekaHelper": "Lother/EurekaHelper",
    "Accountant": "Lother/Accountant",
    "AutoRetainer": "Lother/AutoRetainer",
    "Saucy": "Lother/Saucy",
    "LogogramHelper": "Lother/LogogramHelper",
    "TriadBuddy": "Lother/FFTriadBuddyDalamud",
    "SomethingNeedDoing": "Lother/SomethingNeedDoing",
    "BossModReborn": "Lother/BossmodReborn",
    "WrathCombo": "Lother/WrathCombo",
    "LatihasChocobo": "Lother/LatihasChocobo",
    "Artisan": "Lother/Artisan",
    "Splatoon": "Lother/Splatoon",
    "vnavmesh": "Lother/vnavmesh",
    "InventoryTools": "Lother/InventoryTools",
    "visland": "Lother/visland",
    "Lifestream": "Lother/Lifestream",
    "SubmarineTracker": "Lother/SubmarineTracker",
    "YesAlready": "Lother/YesAlready",
    "GatherbuddyReborn": "Lother/GatherBuddyReborn",
    "ItemVendorLocation": "Lother/ItemVendorLocation",
    "CharacterPanelRefined": "Lother/CharacterPanelRefined",
    "HuntHelper": "Lother/HuntHelper",
    "LazyLoot": "Lother/LazyLoot",
    "MiniMappingway": "Lother/MiniMappingway",
    "NecroLens": "Lother/NecroLens",
    "NotificationMaster": "Lother/NotificationMaster",
    "PalacePal": "Lother/PalacePal",
    "PixelPerfect": "Lother/PixelPerfect",
    "PriceInsight": "Lother/PriceInsight",
    "QoLBar": "Lother/QoLBar",
    "SonarPlugin": "Lother/SonarPlugin",
    "AvantGarde": "Lother/AvantGarde",
    "Dynamis": "Lother/Dynamis",
    # Dynamis's release.yml ships two plugins (Dynamis + the "with hosted
    # PowerShell" variant) from one tag/release - see the asset-matching-by-
    # InternalName logic above for how both get mirrored correctly from a
    # single shared release.
    "DynamisWithSMA": "Lother/Dynamis",
    "ChatTwo": "Lother/ChatTwo",
    "XivTreasureParty": "Lother/XivTreasureParty",
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
    "TriadBuddy": "assets/icon.png",
    "LazyLoot": "images/icon.png",
    "QoLBar": "images/icon.png",
    "MiniMappingway": "images/icon.png",
    # SonarPlugin: no local icon asset upstream (only an external CDN IconUrl);
    # icons/SonarPlugin.png is manually sourced from assets.ffxivsonar.com/dalamud/logo.png.
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

    for internal_name, source_repo in SOURCE_REPOS.items():
        rel = latest_release(source_repo)
        if rel is None:
            print(f"[skip] {internal_name}: no releases found on {source_repo}")
            continue

        tag = rel["tag"]
        if state.get(internal_name) == tag:
            print(f"[up to date] {internal_name}: {tag}")
            continue

        print(f"[new release] {internal_name}: {tag}")

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            local_files = []
            manifest = None
            # A single release can carry assets for more than one InternalName (e.g.
            # Dynamis's release also ships DynamisWithSMA's zip+json under the same
            # tag) - prefer this key's own <InternalName>.json/.zip when present,
            # falling back to "whatever .json/.zip we find" only if there's just one
            # of each (the common single-plugin-per-repo case).
            own_assets = [a for a in rel["assets"] if Path(a["name"]).stem == internal_name]
            assets_to_fetch = own_assets if own_assets else rel["assets"]
            for asset in assets_to_fetch:
                dest = tmp / asset["name"]
                download_asset(asset["url"], dest)
                local_files.append(dest)
                if asset["name"].endswith(".json") and (not own_assets or Path(asset["name"]).stem == internal_name):
                    manifest = json.loads(dest.read_text(encoding="utf-8"))

            public_tag = f"{internal_name}-{tag}"
            gh("release", "create", public_tag,
               *[str(f) for f in local_files],
               "--repo", PUBLIC_REPO,
               "--title", f"{internal_name} {tag}",
               "--notes", f"Mirrored from {source_repo}@{tag}")

            entry = by_internal.get(internal_name)
            if entry is None:
                print(f"[warn] {internal_name} not present in repo.json, skipping metadata update")
            else:
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
                changed = True

        state[internal_name] = tag

    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if changed:
        REPO_JSON.write_text(json.dumps(repo_json, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("done" if changed else "no repo.json changes")


if __name__ == "__main__":
    main()
