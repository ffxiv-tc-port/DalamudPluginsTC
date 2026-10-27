#!/usr/bin/env python3
"""Poll the plugin repos for new releases and mirror their assets into
this feed repo's own releases, then refresh repo.json.

Normally runs inside the DalamudPluginsTC repo's own GitHub Actions workflow
(feed-mirror.yml, self-hosted runner); it can also be run by hand from a local
checkout. Either way it uses an App token that is never stored in the source
plugin repos. `PUBLIC_REPO` keeps its old name: the feed repo is private too.
"""
import base64
import copy
import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app_token import get_installation_token
import leak_guard

GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_JSON = REPO_ROOT / "repo.json"
STATE_FILE = REPO_ROOT / "scripts" / "release-state.json"
ICONS_DIR = REPO_ROOT / "icons"
PUBLIC_REPO = "ffxiv-tc-port/DalamudPluginsTC"

# ── DownloadLink* 的 URL 形狀 ──────────────────────────────────────
# "browser"   github.com/<repo>/releases/download/<tag>/<asset>；private repo 帶不帶 token 都回 404。
# "asset-api" api.github.com/repos/<repo>/releases/assets/<id>，要帶 Authorization: Bearer ＋
#             Accept: application/octet-stream（少了 Accept 拿到的是資產 metadata 的 JSON，不是 zip）。
#             對 private repo 唯一可用的形狀，所以是預設。
DEFAULT_URL_SHAPE = "asset-api"

# main() 每次呼叫時設定一次的執行期選項。刻意走模組全域、不加進 mirror_one() 的呼叫簽章：
# test_mirror_grading.py 用 5 參數的假函式整個換掉 mirror_one，多塞參數會讓那份測試炸掉。
# main() 在起執行緒池之前就設定好，執行緒只讀不寫，沒有競態。
_url_shape = DEFAULT_URL_SHAPE
_dry_run = False

# InternalName -> source repo (owner/name)
SOURCE_REPOS = {
    "TataruPraise": "ffxiv-tc-port/TataruPraise",
    "Meddle.Plugin": "ffxiv-tc-port/Meddle",
    "Brio": "ffxiv-tc-port/Brio",
    "ScrollableTabs": "ffxiv-tc-port/ScrollableTabs",
    "BOCCHI": "ffxiv-tc-port/BOCCHI",
    "Marketbuddy": "ffxiv-tc-port/Marketbuddy",
    "vfaux": "ffxiv-tc-port/vfaux",
    "Gearsetter": "ffxiv-tc-port/Gearsetter",
    "ICE": "ffxiv-tc-port/ICE",
    "ChilledLeves": "ffxiv-tc-port/ChilledLeves",
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
    # Dynamis 的 release.yml 一個 tag 出兩個插件（Dynamis 與內含 PowerShell 的變體），共用同一顆 release；
    # 各自的資產由 mirror_one 以 InternalName 比對檔名分開。
    "DynamisWithSMA": "ffxiv-tc-port/Dynamis",
    "ChatTwo": "ffxiv-tc-port/ChatTwo",
    "XivTreasureParty": "ffxiv-tc-port/XivTreasureParty",
    "SkipCutscene": "ffxiv-tc-port/SkipCutscene",
    "DailyDuty": "ffxiv-tc-port/DailyDuty",
    "Questionable": "ffxiv-tc-port/Questionable",
    # GatheringPathRenderer 是 Questionable repo 底下的第二個出貨 root（同一個 tag
    # 的 release.yml 一次建兩組資產），跟 Dynamis/DynamisWithSMA 完全同構——見上面
    # DynamisWithSMA 的註解。
    "GatheringPathRenderer": "ffxiv-tc-port/Questionable",
    "TextAdvance": "ffxiv-tc-port/TextAdvance",
    "Crossingway": "ffxiv-tc-port/Crossingway",
    "IINACT": "ffxiv-tc-port/IINACT",
    "TCToolbox": "ffxiv-tc-port/TCToolbox",
    "WondrousTailsSolver": "ffxiv-tc-port/EzWondrousTails",
    # repo 名是 MonsterDex，出貨的 InternalName 是 DeepDungeonDex（上游自己就這樣分），
    # 與 EzWondrousTails／WondrousTailsSolver 同形。
    "DeepDungeonDex": "ffxiv-tc-port/MonsterDex",
    "ExplorersIcebox": "ffxiv-tc-port/Explorers-Icebox",
    "Mappy": "ffxiv-tc-port/Mappy",
    "SortaKinda": "ffxiv-tc-port/SortaKinda",
    "EnemyListDebuffs": "ffxiv-tc-port/EnemyListDebuffs",
    "ZodiacBuddy": "ffxiv-tc-port/ZodiacBuddy",
    "HighFpsPhysicsPlugin": "ffxiv-tc-port/xivlauncher_physics_plugin",
    "PeepingTom": "ffxiv-tc-port/PeepingTom",
    "BigPlayerDebuffs": "ffxiv-tc-port/BigPlayerDebuffs",
    "WaymarkPresetPlugin": "ffxiv-tc-port/WaymarkPresetPlugin",
    "DeathRecap": "ffxiv-tc-port/ffxiv-deathrecap",
    "BetterMountRoulette": "ffxiv-tc-port/BetterMountRoulette",
    "GilDelta": "ffxiv-tc-port/GilDelta",
    "HoardFarm": "ffxiv-tc-port/HoardFarm",
}

# InternalName -> icon path within the source repo (on its default branch).
# 圖示一律鏡像進本 repo 的 icons/：來源 repo 是 private，raw.githubusercontent.com 匿名讀不到；
# sync_icon() 走 `gh api repos/<repo>/contents/<path>` 帶 token 抓，IconUrl 寫本 repo 自己的 raw URL。
ICON_PATHS = {
    "TataruPraise": "TataruPraise/Assets/Icon.png",
    "TCToolbox": "images/icon.png",
    "Meddle.Plugin": "icon.png",
    "Brio": "Resources/Images/BrioIcon.png",
    "ScrollableTabs": "ScrollableTabs/Assets/Icon.png",
    "BOCCHI": "assets/icon.png",
    "Marketbuddy": "Marketbuddy/Marketbuddy.png",
    "vfaux": "icon.png",
    "Gearsetter": "Gearsetter/Gearsetter.png",
    "ICE": "Data/Icon.png",
    "ChilledLeves": "Data/Icon.png",
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
    "Crossingway": "Crossingway/images/icon.png",
    "IINACT": "images/icon.png",
    # Questionable: no local icon asset in the repo, upstream's own manifest points at
    # an external CDN IconUrl (puni.sh); icons/Questionable.png is manually sourced from
    # the actual upstream icon at github.com/qstxiv/icons/raw/main/Questionable.png
    # (converted RGB->RGBA to avoid the broken-icon-question-mark bug).
    "WondrousTailsSolver": "res/icon.png",
    "DeepDungeonDex": "DeepDungeonDex/icon.png",
    "ExplorersIcebox": "Data/icon.png",
    "EnemyListDebuffs": "images/icon.png",
    "ZodiacBuddy": "images/icon.png",
    "PeepingTom": "icon.png",
    "BigPlayerDebuffs": "images/icon.png",
    "WaymarkPresetPlugin": "docs/Images/icon.png",
    "DeathRecap": "images/icon.png",
    # GilDelta: 上游自己就備妥了 512x512 RGBA(colortype 6) 且帶 sRGB/gAMA/pHYs 三個
    # metadata chunk 的 icon.png（缺這三塊在外掛安裝器裡會變成空白圖示）。
    "GilDelta": "images/icon.png",
    # HoardFarm: 上游的圖是 300x300 RGB，已在我方 repo 換成 512x512 RGBA 並補上 sRGB/gAMA/pHYs。
    "HoardFarm": "icon.png",
    # GatheringPathRenderer: 刻意不在這裡列——上游的 repo 裡從沒放過圖示檔，也沒有第三方圖示可以借。
    # 留空不是遺漏：manifest 沒有 IconUrl 時 Dalamud 會退到內建的通用圖示，不是破圖。
}


_APP_TOKEN_CACHE: list = []


def _gh_env():
    """讓 `gh` 用 TCToolBox App 的 installation token，而不是本機登入的個人 PAT。

    🔴 為什麼需要這個：不帶 token 時 `gh release create` 用的是執行環境自己的憑證 ——
    本機手動跑＝使用者本人的登入，release 的 `author.login` 會變成個人帳號，而**改寫
    git 歷史完全碰不到那個欄位**；在 feed-mirror workflow 裡，內建的 token 只管得到
    feed repo 自己，讀不到各插件 repo 的 release。

    拿得到 App token 就用它（release 會顯示成 `tctoolbox[bot]`）；
    拿不到就回 None 讓 `gh` 退回原本行為 —— 不要因為 token 拿不到就整個發版失敗。
    """
    if not _APP_TOKEN_CACHE:
        try:
            _APP_TOKEN_CACHE.append(get_installation_token())
        except Exception:
            _APP_TOKEN_CACHE.append(None)

    token = _APP_TOKEN_CACHE[0]
    if not token:
        return None

    env = os.environ.copy()
    # ⚠️ 兩個都要設：gh 會優先讀 GH_TOKEN，但某些子命令看 GITHUB_TOKEN。
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    return env


def gh(*args, check=True):
    result = subprocess.run([GH, *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=_gh_env())
    if result.returncode != 0:
        if check:
            raise RuntimeError(f"gh {' '.join(args)} failed:\n{result.stderr}")
        # check=False 的呼叫端一律用「空字串＝失敗」判斷。`gh api` 遇到 HTTP 錯誤會把錯誤 JSON 印到 stdout，
        # 原樣回傳會被當成正常輸出（曾把 {"message":"Not Found"…} 寫進 feed 的 Changelog）。
        return ""
    return result.stdout.strip()


def latest_release(repo):
    """這個外掛最新的一個 release（含資產清單），沒有 release 就回 None。

    `published_at` feeds repo.json's LastUpdate, which is what Dalamud's
    changelog page sorts by - without it every entry lands on 1970-01-01.

    🔴 `check=True` 是刻意的（2026-08-18 改；以前是 `check=False`）。舊寫法讓
    「GitHub 回 5xx／被限流／網路斷」與「這個 repo 真的還沒有 release」得到
    **一模一樣的空字串**，兩者一起被印成 `[skip] no releases found` —— 平台抖
    一下就靜默少同步一個外掛，而 run 是全綠的。現在讓它拋出去，交給 main() 的
    逐外掛分級（MirrorReport）處理：有既有 feed 條目就記 [warn] 沿用舊條目，
    真的沒有可沿用的條目才 [FAIL]。
    「真的沒有 release」走的是另一條路：API 回 200 + 空陣列，這裡讓 jq 用
    `if length == 0 then null else ... end` 明確判斷，`gh api --jq` 對這個
    結果印出**空字串**（不是文字 `"null"`——2026-09-17 用兩個剛轉 private、
    尚未發過任何 release 的真實 repo `AutoDuty`/`NecroLens` 現場驗過），照樣落進
    `not out` 那個分支回 `None`。
    🔴 2026-09-17 補：以前的 jq 是 `.assets[]` 直接對 `.[0]` 選出來的物件取
    `.assets` 再展開——`.[0]` 對空陣列會給 `null`，`null.assets` 還是 `null`，
    但 `null[]`（展開一個 null）在 jq 裡是**錯誤**（`cannot iterate over: null`）
    不是靜默給空陣列，`gh api` 因此以非 0 離開碼收尾，被 `check=True` 拋成例外。
    這個分支多年沒被走到（71 個外掛過去全部至少有一顆 release），這兩個
    剛重建的 repo 第一次真的踩到空陣列，才暴露這個潛伏的崩潰路徑——不是
    token 權限問題（App token／個人 token 打同一支 API 得到一樣的 `[]`），
    是這兩個 repo 被刪除重建後 git 標籤跟著重新推送回來了（各 30 個 tag），
    但 GitHub Release 物件從沒被任何一次 CI 重新建立過（release.yml 雖然有
    重新註冊成 `active`，但零執行紀錄——標籤與 Release 是兩種不同的東西，
    `git push --tags` 救不回 Release）。外層 `if length == 0` 擋住這個形狀，
    `.assets // []` 是同一類防禦再補一層（個別 release 物件本身 `assets`
    為 null 的情況，目前沒實例，但代價是零）。
    """
    out = gh("api", f"repos/{repo}/releases", "--jq",
              "if length == 0 then null else "
              "(sort_by(.published_at) | reverse | .[0] "
              "| {tag: .tag_name, published: .published_at, "
              "assets: [(.assets // [])[] | {name, url}]}) end")
    if not out or out == "null":
        return None
    return json.loads(out)


def download_asset(asset_url, dest):
    # 一定要帶 App token 環境：private repo 的資產只有 App 讀得到，而 self-hosted runner
    # 上沒有本機 gh 登入可退回（沒帶時 gh 回 exit 4「set GH_TOKEN」，整輪 mirror 靜默零更新）。
    with open(dest, "wb") as f:
        subprocess.run(
            [GH, "api", asset_url, "-H", "Accept: application/octet-stream"],
            stdout=f, check=True, env=_gh_env(),
        )


def resolve_download_url(repo, tag, asset_name, url_shape, report=None, internal_name="?"):
    """算出 `repo` 的 release `tag` 底下 `asset_name` 這個資產要寫進 feed 的 URL。

    "browser"：現況，`releases/download/` 的匿名重導頁；只在 `repo` 保持 public
    時能用。

    "asset-api"：`api.github.com/repos/<repo>/releases/assets/<id>`。這是唯一
    對 private repo 也吃得通的形狀，但 id 是 GitHub 上傳資產後才配發的，需要
    多一次 API 查詢；🔴 查不到就是 `report.fatal`，**絕不沿用 browser 形狀當
    退路**——沿用等於讓使用者的 Dalamud 拿到一顆切成 private 後保證 404 的
    URL，而且沒有任何錯誤訊息（見 `docs/private-migration-design.md` 四-9）。
    """
    if url_shape == "browser":
        return f"https://github.com/{repo}/releases/download/{tag}/{asset_name}"
    if url_shape != "asset-api":
        raise ValueError(f"unknown url_shape {url_shape!r} (must be 'browser' or 'asset-api')")

    out = gh("api", f"repos/{repo}/releases/tags/{tag}", "--jq",
              "[.assets[] | {name, id}]", check=False)
    assets = []
    if out:
        try:
            assets = json.loads(out)
        except json.JSONDecodeError:
            assets = []
    match = next((a for a in assets if a.get("name") == asset_name), None)
    if match is None:
        msg = (f"asset-api url-shape 需要的 asset id 找不到（release {repo}@{tag}，"
               f"asset {asset_name}）—— 拒絕沿用 browser URL（private repo 對那個形狀"
               "匿名 404，而且沒有任何錯誤訊息）")
        if report is not None:
            report.fatal("asset-id", internal_name, msg)
        else:
            print(f"[FAIL] asset-id {internal_name}: {msg}")
        return None
    return f"https://api.github.com/repos/{repo}/releases/assets/{match['id']}"


def rewrite_download_url(internal_name, public_tag, url_shape, report):
    """算出 `DownloadLinkInstall`/`DownloadLinkUpdate` 在 `url_shape` 下該是什麼——
    不碰 `repo.json` 裡的任何欄位、不查來源外掛 repo、不下載任何東西，只讀
    `PUBLIC_REPO`（這個 repo）自己那顆**已經鏡像好**的 release 的資產清單。

    給「切換 `--url-shape` 那天，把既有條目全部換成新形狀」這個場景用：這些
    條目全部 `[up to date]`（來源沒有新版），正常的鏡像流程因此整批短路，永遠
    不會走到寫 `DownloadLinkInstall` 那幾行——這支函式是專門補那個洞的。呼叫端
    自己讀 `entry.get("DownloadLinkInstall")` 當「改之前」的值來比較/印，這裡
    只負責算「改之後」該是什麼。

    查不到 release 或查不到 .zip 資產、或 `resolve_download_url` 本身失敗，
    一律 `report.fatal` 並回 `None`（呼叫端看到 `None` 就跳過，不寫任何東西——
    絕不沿用舊值硬湊一個假的新值）。
    """
    out = gh("api", f"repos/{PUBLIC_REPO}/releases/tags/{public_tag}",
              "--jq", "[.assets[] | {name, id}]", check=False)
    assets = []
    if out:
        try:
            assets = json.loads(out)
        except json.JSONDecodeError:
            assets = []
    if not out or not assets:
        report.fatal("rewrite-lookup", internal_name,
                     f"public release {PUBLIC_REPO}@{public_tag} 查不到資產（可能還沒"
                     "鏡像過，或 tag 對不上）——跳過重寫，不動這條的 DownloadLink*")
        return None
    zip_asset = next((a["name"] for a in assets
                       if a["name"].endswith(".zip") and not a["name"].endswith("-source.zip")),
                      None)
    if zip_asset is None:
        report.fatal("rewrite-lookup", internal_name,
                     f"public release {PUBLIC_REPO}@{public_tag} 沒有非 source 的 .zip 資產"
                     "——跳過重寫，不動這條的 DownloadLink*")
        return None
    return resolve_download_url(PUBLIC_REPO, public_tag, zip_asset, url_shape, report, internal_name)


MAX_CHANGELOG_COMMITS = 25

# ── Changelog 的流程 commit 排除規則 ────────────────────────────────
# 這些 commit 記錄的是**發版流程自己的動作**，對使用者是純噪音（Dalamud 的更新提示只看得到最前面幾行）。
# 🔴 規則一律寫窄：漏濾只是多一行噪音，誤濾是**使用者看不到這一版真的改了什麼**。每一條都要對得上
#    一個真實出現過的主旨，而且窄到不可能命中功能主旨 —— 所以刻意**沒有**「主旨含 BuildNumber 就濾掉」
#    這種規則（真的改版號機制的 commit 主旨也含它）。

# 第一條的來源：repo 刪除後同名重建時 GitHub 不一定把 .github/workflows/*.yml 註冊成 workflow，
# 空 commit 觸發不了重掃，只能改動 workflow 檔本身推一顆「重掛後觸發 GitHub Actions 重新註冊…」的 commit。
CHANGELOG_NOISE_PATTERNS = [
    re.compile(r"觸發\s*GitHub\s*Actions.*重新註冊"),
    # 全艦隊壓縮歷史產生的那一顆（主旨由 ~/.claude/tools/fleet/squash_20261011 決定，改主旨要同步改這裡）。
    re.compile(r"^台服移植：彙整歷史為單一 commit$"),
    # 只改 README 的 commit（主旨固定這一句），插件本身沒有變化。
    re.compile(r"^更新 README（程式碼不變）$"),
    # 轉公開時把發版流程改到 GitHub 代管機器的那一顆（主旨由 workflow_patch.py --hosted-public 的批次決定），插件本身沒有變化。
    re.compile(r"^發版流程改用 GitHub 代管機器建置"),
    # 只動註解的整理 commit（主旨一律以「整理註解」開頭），對使用者沒有可見變化。
    # 🔴 這條必須排最後：COMMENT_ONLY_PATTERN 取的是 [-1]。
    re.compile(r"^整理註解"),
]
COMMENT_ONLY_PATTERN = CHANGELOG_NOISE_PATTERNS[-1]
# 一個版本的 commit 全是整理註解時寫這一句，不沿用上一版的 Changelog。
COMMENT_ONLY_CHANGELOG = "整理註解（功能不變）"

# Changelog 的人工來源：{"fallback_when_compare_fails": [行…], "plugins": {InternalName: {"tag": 版號 tag, "lines": [行…]}}}。
# plugins 的條目只在鏡像「恰好那個 tag」時套用；fallback 只在上一版 tag 比不出來時用。
CHANGELOG_OVERRIDES_FILE = REPO_ROOT / "scripts" / "changelog-overrides.json"
_changelog_overrides_cache = []
_changelog_overrides_lock = threading.Lock()
# (source_repo, prev_tag, tag) -> get_changelog 回 None 的原因；resolve_changelog 靠它分辨「比不出來」與「真的沒有」。
_changelog_notes = {}


def _valid_lines(lines):
    return isinstance(lines, list) and bool(lines) and all(
        isinstance(x, str) and x.strip() and LF_CHAR not in x and CR_CHAR not in x for x in lines)


def load_changelog_overrides(report=None):
    """讀 changelog-overrides.json（每個行程一次）。檔案不在＝沒有覆寫；格式不對就警告並當成沒有。"""
    with _changelog_overrides_lock:
        if _changelog_overrides_cache:
            return _changelog_overrides_cache[0]
        data, problem = {"fallback_when_compare_fails": [], "plugins": {}}, None
        if CHANGELOG_OVERRIDES_FILE.exists():
            try:
                loaded = json.loads(CHANGELOG_OVERRIDES_FILE.read_text(encoding="utf-8-sig"))
                fb, plugins = loaded.get("fallback_when_compare_fails", []), loaded.get("plugins", {})
                if (fb and not _valid_lines(fb)) or not isinstance(plugins, dict) or not all(
                        isinstance(v, dict) and isinstance(v.get("tag"), str) and _valid_lines(v.get("lines"))
                        for v in plugins.values()):
                    problem = "欄位形狀不對"
                else:
                    data = {"fallback_when_compare_fails": fb, "plugins": plugins}
            except (OSError, ValueError, AttributeError) as exc:
                problem = f"讀不了：{exc!r}"
        if problem:
            msg = f"changelog-overrides.json {problem}，整份不套用"
            if report is not None:
                report.warn("changelog", "-", msg)
            else:
                print(f"[warn] changelog -: {msg}")
        _changelog_overrides_cache.append(data)
        return data


def _bullets(lines):
    return LF_CHAR.join(f"- {line}" for line in lines)


def resolve_changelog(internal_name, source_repo, prev_tag, tag, changelog, report=None):
    """決定這一版寫進 feed 的 Changelog：指定這個 tag 的覆寫 > compare 的結果 > compare 失敗時的共用文字。

    回 None＝沿用條目既有的 Changelog。上一版 tag 比不出來而又沒有文字可用時記一筆 [warn]，
    不讓「沿用舊 Changelog」靜默發生。
    """
    def warn(msg):
        if report is not None:
            report.warn("changelog", internal_name, msg)
        else:
            print(f"[warn] changelog {internal_name}: {msg}")

    overrides = load_changelog_overrides(report)
    entry = overrides["plugins"].get(internal_name)
    compare_failed = _changelog_notes.get((source_repo, prev_tag, tag)) == "compare-failed"
    if entry is not None:
        if entry["tag"] == tag:
            print(f"[changelog] {internal_name}: 用 changelog-overrides.json 指定給 {tag} 的 {len(entry['lines'])} 行")
            return _bullets(entry["lines"])
        print(f"[changelog] {internal_name}: changelog-overrides.json 指定的是 {entry['tag']}，這次是 {tag}，不套用")
        if compare_failed:
            warn(f"覆寫指定 {entry['tag']} 而這次鏡像的是 {tag}，這個插件專屬的那幾行沒有用上")
    if changelog:
        return changelog
    if not compare_failed:
        return None
    fallback = overrides["fallback_when_compare_fails"]
    if fallback:
        print(f"[changelog] {internal_name}: compare {prev_tag}...{tag} 比不出來，改用 changelog-overrides.json 的共用文字")
        return _bullets(fallback)
    warn(f"compare {prev_tag}...{tag} 比不出來（上一版 tag 不在或沒有共同祖先），沿用舊 Changelog")
    return None


def is_process_commit(subject):
    """這行主旨是不是「發版流程自己的動作」（⇒ 不該進使用者看的 Changelog）。"""
    return any(p.search(subject) for p in CHANGELOG_NOISE_PATTERNS)


def get_changelog(source_repo, prev_tag, tag):
    """Build a bullet-list changelog for repo.json's "Changelog" field from
    the commit messages between the previously-mirrored tag and this one.
    These commits are all authored by Claude during release work, so the
    first line of each message is already a reasonable changelog entry -
    no upstream release-notes body needed (those are just GitHub's
    auto-generated compare links anyway, and point at a private repo the
    end user can't open). Returns None if there's no previous tag to diff
    against (first-ever mirror of this plugin), the API call fails, or every
    commit in the range was filtered out as release-process noise (see
    CHANGELOG_NOISE_PATTERNS) - the caller keeps the existing Changelog."""
    if not prev_tag:
        return None
    out = gh("api", f"repos/{source_repo}/compare/{prev_tag}...{tag}", "--jq",
              '[.commits[].commit.message | split("\\n")[0]] | reverse | .[]', check=False)
    if not out:
        # 空輸出有兩種：API 失敗（tag 不在、沒有共同祖先），或比得出來但中間沒有 commit。
        status = gh("api", f"repos/{source_repo}/compare/{prev_tag}...{tag}", "--jq", ".status", check=False)
        _changelog_notes[(source_repo, prev_tag, tag)] = "no-commits" if status else "compare-failed"
        return None
    lines = [line for line in out.splitlines() if line.strip()]
    kept = [line for line in lines if not is_process_commit(line)]
    if len(kept) != len(lines):
        print(f"[changelog] {source_repo}: 濾掉 {len(lines) - len(kept)} 筆流程 commit")
    if not kept and any(COMMENT_ONLY_PATTERN.search(line) for line in lines):
        print(f"[changelog] {source_repo}: {prev_tag}...{tag} 全是整理註解／流程 commit，Changelog 寫固定說明「{COMMENT_ONLY_CHANGELOG}」")
        return f"- {COMMENT_ONLY_CHANGELOG}"
    lines = kept
    # 全部都是流程 commit 時退回「沒有 changelog」那條既有路徑（回 None）——
    # 呼叫端是 `if changelog:`，所以條目沿用舊的 Changelog，不會被寫成空字串。
    if not lines:
        return None
    if len(lines) > MAX_CHANGELOG_COMMITS:
        lines = lines[:MAX_CHANGELOG_COMMITS] + [f"...and {len(lines) - MAX_CHANGELOG_COMMITS} more commits"]
    return "\n".join(f"- {line}" for line in lines)


_source_archive_cache = {}
_source_archive_locks_guard = threading.Lock()
_source_archive_locks = defaultdict(threading.Lock)
# None＝這個行程還沒建、或 main() 收尾已清掉。只經 _get_source_archive_dir() 建立，
# import 本身不碰檔案系統（別的工具只借 SOURCE_REPOS 也會 import 這支）。
_SOURCE_ARCHIVE_DIR = None
_source_archive_dir_lock = threading.Lock()


def _get_source_archive_dir():
    """source archive 的暫存根目錄，第一次呼叫才建；多個工作執行緒共用同一個。

    路徑要短：Questionable 的 QuestPaths 單一相對路徑就超過 130 字，服務帳號的
    %TEMP% 前綴約 125 字，兩者相加撞 Windows 260 上限（clone 對每個檔報 Filename too long）。
    CI 上優先用 RUNNER_TEMP（約 35 字），目錄名也只留必要資訊。
    """
    global _SOURCE_ARCHIVE_DIR
    with _source_archive_dir_lock:
        if _SOURCE_ARCHIVE_DIR is None:
            _SOURCE_ARCHIVE_DIR = Path(tempfile.mkdtemp(
                prefix="msrc-", dir=os.environ.get("RUNNER_TEMP") or None))
        return _SOURCE_ARCHIVE_DIR


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


def _rmtree_force(path):
    # 3.12 起文件把 onerror 標為棄用、改用 onexc；_clear_readonly_and_retry 對兩者都相容。
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_clear_readonly_and_retry)
    else:
        shutil.rmtree(path, onerror=_clear_readonly_and_retry)


def _cleanup_source_archive_dir():
    """刪掉 source archive 暫存根目錄並清快取（快取的 zip 都在裡面）；沒建過就什麼都不做。

    🔴 刪不掉只印一行 `[cleanup]`、絕不擲例外：這是 main() 的 finally，擲出去會把
    已經寫好的 feed 變成整輪失敗，或蓋掉原本那個例外。前綴刻意不是 [warn]／[FAIL]
    （workflow 只對那兩個行首標記分級）。
    """
    global _SOURCE_ARCHIVE_DIR
    with _source_archive_dir_lock:
        root, _SOURCE_ARCHIVE_DIR = _SOURCE_ARCHIVE_DIR, None
        _source_archive_cache.clear()
    if root is None:
        return
    try:
        _rmtree_force(root)
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] 暫存目錄沒刪乾淨（不影響 feed）：{root}: {_flatten(exc)}")


def build_source_archive(source_repo, tag):
    """Clone the tagged commit (with submodules) from the private source repo,
    strip every .git directory (top-level and every submodule's own - these
    hold real commit-author identity, not just history noise), and zip the
    result. Cached per (source_repo, tag) since Dynamis/DynamisWithSMA share
    one source repo/tag across two InternalNames - the cache and its backing
    files must outlive any single per-release TemporaryDirectory, so this
    uses its own directory (_get_source_archive_dir()) that lives until main()
    returns, not the caller's per-iteration `tmp`. Thread-safe: callers
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

        workdir = _get_source_archive_dir() / f"{source_repo.rsplit('/', 1)[-1]}-{tag}"
        workdir.mkdir(parents=True, exist_ok=True)
        clone_dir = workdir / "s"
        # 前一個同 key 的呼叫端 clone 失敗會留下半套目錄，git 對非空目錄直接拒絕。
        if clone_dir.exists():
            shutil.rmtree(clone_dir, onerror=_clear_readonly_and_retry)
        result = subprocess.run(
            [GH, "repo", "clone", source_repo, str(clone_dir), "--",
             "-c", "core.longpaths=true",
             "--recurse-submodules", "--depth", "1", "--branch", tag],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=_gh_env(),
        )
        if result.returncode != 0:
            # [warn] 那一行會被壓到 400 字，clone 的真正錯誤常在 stderr 尾端；另印一行完整版。
            print(f"[detail] clone stderr {source_repo}@{tag}: {_flatten(result.stderr, 4000)}")
            shutil.rmtree(clone_dir, onerror=_clear_readonly_and_retry)
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

        # Not a leak risk like .git, but not "source" either - trim repo/CI/git-plumbing
        # meta-files (top-level and every submodule's own). .gitmodules in particular would
        # describe every submodule as an external reference to fetch separately, which
        # defeats the point of physically bundling the submodules' own source inline.
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


def sync_icon(internal_name, source_repo, entry, report=None):
    icon_path = ICON_PATHS.get(internal_name)
    if not icon_path:
        return False
    out = gh("api", f"repos/{source_repo}/contents/{icon_path}", "--jq", ".content", check=False)
    if not out:
        # 圖示是純外觀：抓不到就沿用 icons/ 裡的舊檔與條目既有的 IconUrl，
        # 版本與下載連結完全不受影響 —— 一律 warn，不算「這個外掛失敗」。
        msg = f"could not fetch icon at {icon_path}"
        if report is not None:
            report.warn("icon", internal_name, msg)
        else:
            print(f"[warn] {internal_name}: {msg}")
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


# ── 逐外掛失敗分級 ────────────────────────────────────────────────
# 單一外掛的暫時性錯誤不能擋住所有人的更新：[warn] 繼續跑、[FAIL] 停下不寫 feed。
# 🔴 離開碼只由 __main__ 決定，不由 main() 決定：release_plugin.py 是 import 進來直接呼叫 main() 的。

# [warn] mirror        單一外掛抓 release／資產／上傳失敗，而它在 repo.json **有**既有條目 → 把該條目與
#                      release-state 還原成這一輪開始前的樣子（不留半套資料），其餘外掛照常處理。
# [warn] icon          圖示同步失敗（純外觀）。
# [warn] orphan        release 鏡像成功但 repo.json 沒有條目。

# [FAIL] all-failed    ① 所有外掛都失敗 —— 平台級問題，寫 feed 沒有意義。
# [FAIL] persist       ② repo.json／release-state.json 寫入失敗（feed 本身壞了）。
# [FAIL] no-feed-entry ③ 失敗的外掛在 repo.json 沒有既有條目可沿用 —— 新外掛首發不能靜默漏。
# [FAIL] unexpected    分級邏輯本身有洞（不該發生 → 當硬失敗，不要吞）。


class FeedWriteError(RuntimeError):
    """repo.json／release-state.json 寫不進去 —— feed 本身壞了，硬失敗。"""


def _flatten(text, limit=400):
    """把例外訊息壓成單行。

    🔴 為什麼一定要壓：`gh()` 的 RuntimeError 直接夾了多行 stderr，原樣印出去會
    讓「行首標記」這個契約破功（workflow 的保險就是對行首的 [FAIL] 做 grep），
    續行的內容還可能長得像另一個標記。壓成單行之後，一筆結果永遠只佔一行。
    """
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + " ...(截斷)"


CR_CHAR = chr(13)
LF_CHAR = chr(10)


def _gha_escape(text):
    """GitHub Actions 的 workflow command 資料段跳脫（%, CR, LF）。"""
    return (str(text).replace("%", "%25")
                     .replace(CR_CHAR, "%0D")
                     .replace(LF_CHAR, "%0A"))


class MirrorReport:
    """一輪 mirror 的逐外掛結果。執行緒安全（8 個 worker 同時在寫）。"""

    def __init__(self, targets=0):
        self.targets = targets
        self._lock = threading.Lock()
        self.ok = []
        self.warns = []   # [(kind, name, reason)]
        self.fatals = []  # [(kind, name, reason)]

    def add_ok(self, name):
        with self._lock:
            self.ok.append(name)

    def warn(self, kind, name, reason):
        reason = _flatten(reason)
        with self._lock:
            self.warns.append((kind, name, reason))
        print(f"[warn] {kind} {name}: {reason}")

    def fatal(self, kind, name, reason):
        reason = _flatten(reason)
        with self._lock:
            self.fatals.append((kind, name, reason))
        print(f"[FAIL] {kind} {name}: {reason}")

    @property
    def failed_plugins(self):
        """外掛層級失敗的名字集合。圖示與 orphan 不算 —— 它們不影響版本/下載連結，
        拿它們去湊「全部都失敗」會把一次正常的 run 誤判成平台事故。"""
        with self._lock:
            return ({n for k, n, _ in self.warns if k == "mirror"}
                    | {n for k, n, _ in self.fatals if k in ("mirror", "no-feed-entry")})

    @property
    def hard_fail(self):
        with self._lock:
            return bool(self.fatals)

    def print_tail(self):
        """把結果重印一次在 log 尾巴，這樣光看 log 最後 20 行就知道發生什麼事。
        前綴 `[summary]` 是刻意的：workflow 那道「行首 [FAIL] 就停」的保險不能被
        這裡的重印餵出假陽性。"""
        print("")
        print("=== mirror 結果 ===")
        print(f"[summary] targets={self.targets} ok={len(self.ok)} "
              f"warn={len(self.warns)} fatal={len(self.fatals)}")
        for kind, name, reason in self.warns:
            print(f"[summary][warn] {kind} {name}: {reason}")
        for kind, name, reason in self.fatals:
            print(f"[summary][FAIL] {kind} {name}: {reason}")

    def _table(self, rows):
        out = ["| 外掛 | 類別 | 原因 |", "| --- | --- | --- |"]
        for kind, name, reason in rows:
            cells = [str(c).replace("|", "\\|") for c in (name, kind, reason)]
            out.append("| " + " | ".join(cells) + " |")
        return out

    def emit_github(self):
        """寫 run summary 與 annotation。本機跑（release_plugin.py 的退路模式）時
        這兩個環境變數都不存在，等於沒作用。

        🔑 這個區塊是整份 summary 的**第一段**（後面的步驟才會往下追加），所以
        「有 warn」與「全綠」在 run 頁面第一眼就分得出來。"""
        if os.environ.get("GITHUB_ACTIONS"):
            for kind, name, reason in self.warns:
                print(f"::warning title=mirror {kind} {name}::{_gha_escape(reason)}")
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        if not path:
            return
        lines = []
        if self.fatals:
            lines.append(f"## 🔴 mirror 硬失敗（{len(self.fatals)} 筆）— feed **沒有**更新")
            lines.append("")
            lines += self._table(self.fatals)
            if self.warns:
                lines.append("")
                lines.append(f"另有 {len(self.warns)} 筆警告：")
                lines.append("")
                lines += self._table(self.warns)
        elif self.warns:
            lines.append(f"## ⚠️ 有 {len(self.warns)} 筆警告（不是全綠）")
            lines.append("")
            lines.append(f"其餘 {len(self.ok)} 個外掛照常更新。`mirror` 類的警告代表該外掛"
                         "**沿用 feed 既有條目**（沒有寫入半套資料），下一班（15 分鐘後）"
                         "自動重試。")
            lines.append("")
            lines += self._table(self.warns)
        else:
            lines.append(f"## ✅ mirror 全綠（{self.targets} 個外掛，0 警告）")
        with open(path, "a", encoding="utf-8") as f:
            f.write(LF_CHAR.join(lines) + LF_CHAR)


# 出貨包自帶 Newtonsoft.Json.dll 的插件，Dalamud 存讀設定時不認得它的 [JsonIgnore]／[JsonProperty]／
# [JsonConverter]（插件與 Dalamud 各有一份 Newtonsoft，屬性的型別身分不同），而且是靜默的。
# 下面這幾個已知自帶、設定型別上沒有這些屬性；其餘的一出現就記 [warn]（只提醒、不擋）。
BUNDLED_NEWTONSOFT_KNOWN = frozenset(
    {"ItemVendorLocation", "DynamisWithSMA", "DailyDuty", "SortaKinda", "Mappy"})


def note_bundled_newtonsoft(internal_name, files, report=None):
    """回傳這批資產的 zip 裡有沒有自帶 Newtonsoft.Json.dll；有就印一行，不在已知名單的另記 warn。"""
    bundled = False
    for f in files:
        if not str(f).lower().endswith(".zip"):
            continue
        try:
            with zipfile.ZipFile(f) as z:
                names = z.namelist()
        except (OSError, zipfile.BadZipFile):
            continue
        if any(n.replace("\\", "/").rsplit("/", 1)[-1].lower() == "newtonsoft.json.dll" for n in names):
            bundled = True
    if not bundled:
        return False
    reason = ("出貨包自帶 Newtonsoft.Json.dll：Dalamud 存讀設定時不認得這個插件的 "
              "[JsonIgnore]／[JsonProperty]（csproj 對 Newtonsoft.Json 設 ExcludeAssets=runtime 可解）")
    if internal_name in BUNDLED_NEWTONSOFT_KNOWN or report is None:
        print(f"[note] bundled-newtonsoft {internal_name}: {reason}")
    else:
        report.warn("bundled-newtonsoft", internal_name, reason)
    return True


def mirror_one(internal_name, source_repo, state, by_internal, report=None):
    """Mirror one plugin's latest release (binary + source archive) into the
    feed repo and update its repo.json entry in place. Returns True if
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
        # Case-INSENSITIVE stem match: GatherbuddyReborn publishes its manifest as
        # "GatherBuddyReborn.json" (capital B) while its InternalName is "GatherbuddyReborn".
        # A case-sensitive match silently skips the manifest, so Description / Author /
        # DalamudApiLevel never sync - and DalamudApiLevel is what Dalamud uses to decide
        # whether to offer the plugin at all. No two InternalNames collide when lowercased.
        own_assets = [a for a in rel["assets"]
                      if Path(a["name"]).stem.lower() == internal_name.lower()]
        assets_to_fetch = own_assets if own_assets else rel["assets"]

        existing = gh("release", "view", public_tag, "--repo", PUBLIC_REPO,
                       "--json", "assets", "--jq", "[.assets[].name]", check=False)
        release_exists = existing != ""
        existing_asset_names = json.loads(existing) if existing else []
        has_source_asset = f"{internal_name}-source.zip" in existing_asset_names

        if already_current and release_exists and has_source_asset:
            print(f"[up to date] {internal_name}: {tag}")
            return False

        if _dry_run:
            # 只印會寫什麼，不建 release、不下載、不改 repo.json/release-state.json。
            # 與下面正式路徑挑同一顆：共用 release 的插件（Dynamis／DynamisWithSMA）只看自己的資產。
            zip_asset = next((a["name"] for a in assets_to_fetch if a["name"].endswith(".zip")), None)
            print(f"[dry-run] {internal_name}: {source_repo}@{tag} -> public tag {public_tag} "
                  f"(release_exists={release_exists}, url_shape={_url_shape})")
            if zip_asset is None:
                print(f"[dry-run] {internal_name}: 來源 release 沒有 .zip 資產，沒有 URL 可算")
                return False
            if not release_exists:
                # asset-api 形狀的 id 是 GitHub 上傳資產後才配發的 —— 這個 tag 還沒鏡像過，
                # 沒有真的 id 可查，印出來會是假資料，所以只講清楚這個限制，不硬造一個。
                print(f"[dry-run] {internal_name}: public release {public_tag} 還不存在，"
                      "asset id 要等真的鏡像過一次才查得到，這裡不預測")
                return False
            url = resolve_download_url(PUBLIC_REPO, public_tag, zip_asset, _url_shape,
                                        report, internal_name)
            if url is not None:
                print(f"[dry-run] {internal_name}: DownloadLinkInstall/DownloadLinkUpdate -> {url}")
            return False

        print(f"[new release] {internal_name}: {tag}" if not already_current
              else f"[fixing up] {internal_name}: {tag} (missing source asset)")

        for asset in assets_to_fetch:
            is_manifest = asset["name"].endswith(".json") and (
                not own_assets
                or Path(asset["name"]).stem.lower() == internal_name.lower())
            # When the public release already carries every asset, the only thing
            # still needed from the source release is the manifest (to refresh
            # repo.json) - don't re-download multi-MB zips just to throw them away.
            if release_exists and not is_manifest:
                continue
            dest = tmp / asset["name"]
            download_asset(asset["url"], dest)
            local_files.append(dest)
            if is_manifest:
                manifest = json.loads(dest.read_text(encoding="utf-8"))

        note_bundled_newtonsoft(internal_name, local_files, report)

        # Bundle a matching source snapshot alongside the binary, uniformly for every
        # plugin regardless of upstream license. .git is stripped (top-level + every
        # submodule's own) so this can't leak commit-author identity.
        # Only built when it's actually going to be uploaded - a full recursive clone of
        # the tagged commit is the single most expensive operation in the script.
        named_src_zip = None
        if not release_exists or not has_source_asset:
            src_zip = build_source_archive(source_repo, tag)
            named_src_zip = tmp / f"{internal_name}-source.zip"
            if src_zip != named_src_zip:
                shutil.copyfile(src_zip, named_src_zip)

        # 上傳前過帳號痕跡閘門；擋下時丟例外，交給 run_and_persist 還原這個插件的既有條目
        to_upload = ([str(f) for f in local_files] if not release_exists else []) + \
            ([str(named_src_zip)] if named_src_zip else [])
        if to_upload:
            ok, why = leak_guard.check_files(to_upload)
            print(f"[leak-guard] {internal_name}: {why}")
            if not ok:
                raise RuntimeError(f"帳號痕跡閘門不放行上傳：{why}")

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
            # Deliberately do NOT record state here. Recording it would make the next run
            # see already_current==True and short-circuit to "[up to date]", so the metadata
            # would never be written even after somebody adds the repo.json entry. Leaving
            # state unset costs one repeated asset download next run and then self-heals.
            msg = ("no repo.json entry - release mirrored, but metadata NOT synced "
                   "and state NOT recorded. Add the entry and re-run; it will pick "
                   "up from here.")
            # ⚠️ 這是**成功路徑**的警告（release 真的鏡像好了），跟分級規則 ③ 的
            # no-feed-entry 不是同一件事 —— 那個是「失敗且沒有舊條目可沿用」。
            # 刻意維持 warn：升成硬失敗會讓一個少填的條目擋住其他所有外掛的 feed。
            if report is not None:
                report.warn("orphan", internal_name, msg)
            else:
                print(f"[warn] {internal_name}: {msg}")
            return True

        if manifest:
            for key in ("AssemblyVersion", "Description", "Punchline", "Author",
                        "DalamudApiLevel"):
                if key in manifest:
                    entry[key] = manifest[key]
            # Keep the testing level in lockstep with the real one so the era
            # bump (API12->13) can't leave a stale testing filter behind.
            if "DalamudApiLevel" in manifest and "TestingDalamudApiLevel" in entry:
                entry["TestingDalamudApiLevel"] = manifest["DalamudApiLevel"]
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
            url = resolve_download_url(PUBLIC_REPO, public_tag, zip_asset, _url_shape,
                                        report, internal_name)
            if url is None:
                # resolve_download_url 已經對 report 記過 fatal 了。這裡用例外把它接進
                # run_and_persist() 既有的「還原這個外掛的條目與 state」路徑 —— 絕不能
                # 讓 entry 帶著舊的 DownloadLinkInstall 或半套資料被 persist() 寫出去。
                raise RuntimeError(f"resolve_download_url 失敗（{internal_name}），見上面的 [FAIL]")
            entry["DownloadLinkInstall"] = url
            entry["DownloadLinkUpdate"] = url

        changelog = get_changelog(source_repo, prev_tag, tag)
        changelog = resolve_changelog(internal_name, source_repo, prev_tag, tag, changelog, report)
        if changelog:
            entry["Changelog"] = changelog

        # Dalamud's changelog page builds its entries straight off the manifest
        # and sorts them by LastUpdate (Unix seconds). Missing field -> the
        # constructor falls back to epoch 0, so every plugin shows 1970-01-01
        # and the ordering carries no information.
        published = rel.get("published")
        if published:
            try:
                ts = datetime.datetime.strptime(
                    published, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=datetime.timezone.utc)
                entry["LastUpdate"] = int(ts.timestamp())
            except ValueError:
                pass  # keep whatever was there rather than writing a bogus date

        state[internal_name] = tag
        return True


def _run_rewrite_urls(targets, by_internal, state, url_shape, dry_run, report, persist):
    """`--rewrite-urls` 的主體：只重寫既有條目的 `DownloadLinkInstall`/
    `DownloadLinkUpdate` 兩個欄位，其他欄位（含 `RepoUrl`）一個字元不動。

    為什麼要獨立於正常的鏡像流程：正常流程靠「來源 repo 有沒有新 tag」決定
    要不要動 `DownloadLinkInstall`——[up to date] 的條目整批短路，永遠不會
    走到寫那兩行。切換 `--url-shape` 那天，71 筆條目幾乎全部 [up to date]
    （來源沒有新版，只是想換 URL 的形狀），正常流程完全補不到這個洞。
    這裡改成直接讀 `release-state.json` 記錄的 tag，只打 `PUBLIC_REPO` 自己
    的 API，完全不碰來源外掛 repo（也就不需要來源 repo 的 App token 權限）。
    """
    for name in sorted(targets):
        entry = by_internal.get(name)
        if entry is None:
            report.warn("rewrite-skip", name, "repo.json 沒有這個條目，跳過")
            continue
        tag = state.get(name)
        if not tag:
            report.warn("rewrite-skip", name,
                        "release-state.json 沒有記錄過的 tag（還沒鏡像過），跳過")
            continue
        public_tag = f"{name}-{tag}"
        old_url = entry.get("DownloadLinkInstall")
        new_url = rewrite_download_url(name, public_tag, url_shape, report)
        if new_url is None:
            continue  # rewrite_download_url 已經記過 report.fatal 了
        if new_url == old_url:
            print(f"[rewrite] {name}: 已經是 {url_shape} 形狀，不用改")
            report.add_ok(name)
            continue
        if dry_run:
            print(f"[dry-run][rewrite] {name}:\n"
                  f"    before: {old_url}\n"
                  f"    after:  {new_url}")
            report.add_ok(name)
            continue
        entry["DownloadLinkInstall"] = new_url
        entry["DownloadLinkUpdate"] = new_url
        print(f"[rewrite] {name}: {old_url} -> {new_url}")
        report.add_ok(name)
        persist(True)
    return report


def main(only=None, url_shape=DEFAULT_URL_SHAPE, dry_run=False, rewrite_urls=False):
    """Mirror releases into this repo's releases + repo.json.

    `only` restricts the run to specific InternalNames; None sweeps everything.
    A full sweep costs >= 2 `gh` calls per plugin (latest_release + release view)
    plus one per icon - a few hundred round-trips across the whole feed even when
    nothing has changed - so release_plugin.py passes just the plugins it released.

    `url_shape`："asset-api"（預設；private repo 只有這個形狀下載得到）或 "browser"
    （見 `resolve_download_url` 的說明）。`dry_run`＝True 時完全不建 release、不
    下載資產、不動圖示、不寫 repo.json／release-state.json —— 只印出每個外掛
    「會寫什麼」，給切換前驗證用。

    `rewrite_urls`＝True 時完全跳過正常的「查來源 repo 有沒有新版」流程，改走
    `_run_rewrite_urls()`：只重寫既有條目的 `DownloadLinkInstall`/
    `DownloadLinkUpdate`（見該函式說明，這是切換 `url_shape` 當天要用的模式，
    不是日常同步用的）。

    🔴 `url_shape`/`dry_run` 刻意**不**加進 `mirror_one(...)` 的呼叫簽章
    （改用模組全域 `_url_shape`/`_dry_run`）——`test_mirror_grading.py` 用一個
    5 參數的假函式整個換掉 `mirror_one`，呼叫簽章多塞參數會讓那份測試炸掉。

    回傳 MirrorReport（見上面的分級規則）。🔴 **不會**因為逐外掛失敗而拋例外、
    也**不會**自己決定離開碼 —— release_plugin.py 的 --wait 退路模式是 import
    進來直接呼叫這個函式的，它的行為必須跟加固前一樣。離開碼只在 __main__ 決定。
    """
    # source archive 暫存目錄不論正常結束、例外或 --rewrite-urls 提早返回都在這裡清。
    try:
        return _main_body(only, url_shape, dry_run, rewrite_urls)
    finally:
        _cleanup_source_archive_dir()


def _main_body(only, url_shape, dry_run, rewrite_urls):
    if url_shape not in ("browser", "asset-api"):
        raise ValueError(f"unknown url_shape {url_shape!r} (must be 'browser' or 'asset-api')")

    global _url_shape, _dry_run
    _url_shape = url_shape
    _dry_run = dry_run

    state = load_json(STATE_FILE, {})
    repo_json = load_json(REPO_JSON, [])
    by_internal = {e["InternalName"]: e for e in repo_json}

    targets = {n: r for n, r in SOURCE_REPOS.items()
               if only is None or n in set(only)}
    report = MirrorReport(len(targets))

    if only is not None:
        unknown = sorted(set(only) - set(SOURCE_REPOS))
        if unknown:
            report.warn("unknown-name", ",".join(unknown),
                        "not in SOURCE_REPOS, ignoring")

    def persist(also_repo_json):
        # state/repo_json/by_internal are shared across threads - only the
        # persistence (and the entry mutations inside mirror_one, which all
        # happen before this point in the same call) need serializing.
        if _dry_run:
            print(f"[dry-run] 會寫 release-state.json"
                  f"{' 與 repo.json' if also_repo_json else ''}，這裡略過")
            return
        with _persist_lock:
            try:
                STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
                if also_repo_json:
                    REPO_JSON.write_text(json.dumps(repo_json, indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                # 分級規則 ②：feed 檔案寫不進去，跟「某個外掛抓不到 release」是
                # 完全不同的等級 —— 繼續跑只會產出一份殘缺的 feed。
                report.fatal("persist", "-", f"feed 檔案寫入失敗: {exc!r}")
                raise FeedWriteError(str(exc)) from exc

    if rewrite_urls:
        print(f"[rewrite-urls] url_shape={url_shape}；只改 DownloadLinkInstall/Update，"
              "其他欄位（含 RepoUrl）不動" + ("（dry-run，不寫檔）" if dry_run else ""))
        _run_rewrite_urls(targets, by_internal, state, url_shape, dry_run, report, persist)
        report.print_tail()
        report.emit_github()
        return report

    if dry_run:
        print(f"[dry-run] url_shape={url_shape}；跳過圖示同步（純外觀，與 URL 形狀無關）、"
              "不寫 repo.json / release-state.json")

    changed = False

    # Icon sync runs in parallel: each call touches a different plugin's own repo.json
    # entry and its own icons/<name>.png, so there is nothing to serialize.
    # 🔴 dry_run 時整個跳過：圖示同步跟 DownloadLink* 的 URL 形狀無關，而
    # sync_icon() 一旦抓到新圖示就會就地寫 icons/<name>.png 到磁碟 —— 那是
    # 貨真價實的寫入，"只印會寫什麼" 的承諾涵蓋它。
    if not dry_run:
        with ThreadPoolExecutor(max_workers=8) as pool:
            icon_futures = {
                pool.submit(sync_icon, name, repo, by_internal[name], report): name
                for name, repo in targets.items() if name in by_internal
            }
            for future in as_completed(icon_futures):
                name = icon_futures[future]
                try:
                    if future.result():
                        print(f"[icon updated] {name}")
                        changed = True
                except Exception as exc:  # noqa: BLE001
                    # sync_icon 只有在寫檔成功之後才碰 entry["IconUrl"]，所以這裡不
                    # 需要還原條目。
                    report.warn("icon", name, exc)
        if changed:
            persist(True)

    def run_and_persist(internal_name, source_repo):
        # 🔴 快照要在動任何東西**之前**拍。mirror_one 是就地改 by_internal 裡那個
        # dict（它就是 repo_json 清單裡的同一個物件），而 persist() 是逐外掛增量
        # 寫檔的 —— 別的執行緒隨時會把「這個外掛改到一半」的樣子寫進 repo.json。
        entry = by_internal.get(internal_name)
        with _persist_lock:
            entry_snapshot = copy.deepcopy(entry) if entry is not None else None
            state_snapshot = state.get(internal_name)

        try:
            result = mirror_one(internal_name, source_repo, state, by_internal, report)
        except Exception as exc:  # noqa: BLE001
            with _persist_lock:
                if entry_snapshot is not None:
                    # clear()+update() 而不是換掉物件：repo_json 清單裡存的是同一個
                    # 參考，換物件還原不到已經寫出去的那份。
                    entry.clear()
                    entry.update(entry_snapshot)
                if state_snapshot is None:
                    state.pop(internal_name, None)
                else:
                    state[internal_name] = state_snapshot
            if entry_snapshot is None:
                report.fatal("no-feed-entry", internal_name,
                             f"同步失敗，而 repo.json 沒有既有條目可沿用（新外掛首發？）: {exc}")
            else:
                report.warn("mirror", internal_name,
                            f"同步失敗，沿用 feed 既有條目 "
                            f"{entry_snapshot.get('AssemblyVersion', '?')}: {exc}")
            # 🔴 還原完一定要再寫一次檔：這個外掛可能是最後一個結束的，而別的
            # 執行緒早就把它半套的樣子寫進 repo.json 了 —— 只改記憶體不刷回磁碟
            # 等於沒還原。
            persist(True)
            return False

        report.add_ok(internal_name)
        persist(result)
        return result

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(run_and_persist, name, repo): name
            for name, repo in targets.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                if future.result():
                    changed = True
            except FeedWriteError:
                pass  # persist() 已經記成 fatal 了，不要重複記
            except Exception as exc:  # noqa: BLE001
                # run_and_persist 已經把所有可預期的失敗分級掉了；走到這裡代表
                # 分級邏輯自己有洞 —— 當硬失敗，不要靜默吞掉。
                report.fatal("unexpected", name, exc)

    # 分級規則 ①：全部都失敗＝平台級問題。這時候寫 feed 沒有意義，硬失敗停下來。
    failed = report.failed_plugins
    if targets and len(failed) == len(targets):
        report.fatal("all-failed", "*",
                     f"{len(targets)} 個外掛全部失敗 —— 判定為平台級問題，這一輪不寫 feed")

    scope = f"{len(targets)} plugin(s) checked"
    print(f"done ({scope})" if changed else f"no repo.json changes ({scope})")
    report.print_tail()
    report.emit_github()
    return report


def _selftest():
    """`--selftest`：source archive 暫存目錄的生命週期，離線、合成資料。

    不打網路（`gh`／token 換成會記錄呼叫的替身，最後斷言零呼叫）、不寫 repo 裡的
    repo.json／release-state.json（前後比雜湊）。exit 0＝PASS、2＝FAIL。
    """
    import contextlib
    import hashlib
    import io
    import subprocess as real_subprocess
    import zipfile

    mod = sys.modules[__name__]
    scripts_dir = Path(__file__).resolve().parent
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
        if not cond:
            fails.append(label)

    def digest(p):
        return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None

    def msrc_dirs(parent):
        return sorted(p for p in Path(parent).glob("msrc-*") if p.is_dir())

    real_digests = {p: digest(p) for p in (REPO_JSON, STATE_FILE)}
    patched = ("REPO_JSON", "STATE_FILE", "ICONS_DIR", "SOURCE_REPOS", "ICON_PATHS",
               "mirror_one", "sync_icon", "gh", "get_installation_token", "_gh_env",
               "subprocess", "_run_rewrite_urls", "_rmtree_force")
    saved = {k: getattr(mod, k) for k in patched}
    env_keys = ("RUNNER_TEMP", "GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS")
    saved_env = {k: os.environ.get(k) for k in env_keys}
    forbidden_calls = []

    def forbid(name):
        def _stub(*_a, **_k):
            forbidden_calls.append(name)
            raise RuntimeError(f"selftest 不該呼叫 {name}")
        return _stub

    base = Path(tempfile.mkdtemp(prefix="mirror-selftest-"))
    try:
        mod.gh = forbid("gh")
        mod.get_installation_token = forbid("get_installation_token")
        mod._gh_env = lambda: None
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        os.environ.pop("GITHUB_ACTIONS", None)
        runner_temp = base / "runner-temp"
        runner_temp.mkdir()
        os.environ["RUNNER_TEMP"] = str(runner_temp)

        # 1. import 不建目錄：本行程早就 import 過了，另起行程。正對照證明計數法看得到 mkdtemp。
        child_env = {k: v for k, v in os.environ.items() if not k.startswith("TCTOOLBOX_APP_")}

        def child(code, sub):
            d = base / sub
            d.mkdir()
            env = dict(child_env, RUNNER_TEMP=str(d), TEMP=str(d), TMP=str(d))
            r = real_subprocess.run([sys.executable, "-B", "-c", code, str(scripts_dir)],
                                    capture_output=True, env=env)
            return r.returncode, sorted(x.name for x in d.iterdir()), r.stderr.decode("utf-8", "replace")

        rc, made, err = child("import os, tempfile; tempfile.mkdtemp(prefix='msrc-', "
                              "dir=os.environ.get('RUNNER_TEMP') or None)", "import-control")
        check("正對照：另起行程 mkdtemp 一次 ⇒ 數得到 1 個", rc == 0 and len(made) == 1, f"{made} {err[-200:]}")
        rc, made, err = child("import sys; sys.path.insert(0, sys.argv[1]); import mirror_releases",
                              "import-probe")
        check("import mirror_releases ⇒ 不建任何目錄", rc == 0 and made == [], f"rc={rc} {made} {err[-300:]}")

        # 2. 第一次要才建；多執行緒同時要只建一個，而且建在 RUNNER_TEMP 底下。
        check("一開始沒有暫存目錄", mod._SOURCE_ARCHIVE_DIR is None, repr(mod._SOURCE_ARCHIVE_DIR))
        barrier = threading.Barrier(8)
        got = []

        def grab():
            barrier.wait()
            got.append(mod._get_source_archive_dir())

        workers = [threading.Thread(target=grab) for _ in range(8)]
        for t in workers:
            t.start()
        for t in workers:
            t.join()
        dirs = msrc_dirs(runner_temp)
        check("8 個執行緒同時要 ⇒ 同一個目錄、只建一個", len(set(got)) == 1 and len(dirs) == 1,
              f"{len(set(got))} {dirs}")
        check("建在 RUNNER_TEMP 底下、前綴 msrc-",
              len(dirs) == 1 and dirs[0].parent == runner_temp and got[0] == dirs[0], str(dirs))

        # 3. 清理：git pack 檔在 Windows 是唯讀的。負對照：ignore_errors=True 刪不掉同一個結構。
        def readonly_fixture(root):
            f = root / "Fake-v1" / "s" / "objects" / "pack" / "x.idx"
            f.parent.mkdir(parents=True)
            f.write_bytes(b"x")
            os.chmod(f, stat.S_IREAD)
            return f

        if os.name == "nt":
            neg = base / "negative"
            neg_file = readonly_fixture(neg)
            shutil.rmtree(neg, ignore_errors=True)
            check("負對照：ignore_errors=True 刪不掉唯讀檔（夾具有效）", neg_file.exists())
        root = got[0]
        readonly_fixture(root)
        mod._source_archive_cache[("ffxiv-tc-port/Fake", "v1")] = root / "stale.zip"
        mod._cleanup_source_archive_dir()
        check("清理：含唯讀檔的目錄整個刪掉", not root.exists())
        check("清理：快取清空、狀態回 None",
              not mod._source_archive_cache and mod._SOURCE_ARCHIVE_DIR is None)
        mod._cleanup_source_archive_dir()
        check("沒建過時再清一次是 no-op", mod._SOURCE_ARCHIVE_DIR is None and not msrc_dirs(runner_temp))

        # 4. 真的 build_source_archive（clone 換成本機合成 repo）跑在 main() 裡，main() 結束後全清。
        origin = base / "origin"
        (origin / "src").mkdir(parents=True)
        (origin / "src" / "a.cs").write_text("class A {}\n", encoding="utf-8")
        (origin / ".github" / "workflows").mkdir(parents=True)
        (origin / ".github" / "workflows" / "x.yml").write_text("on: push\n", encoding="utf-8")
        (origin / ".gitignore").write_text("bin/\n", encoding="utf-8")
        git_id = ["-c", "user.name=selftest", "-c", "user.email=selftest@invalid"]
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "selftest"], ["tag", "v1"]):
            real_subprocess.run(["git", "-C", str(origin), *git_id, *args], check=True, capture_output=True)

        clone_calls = []

        class CloneShim:
            @staticmethod
            def run(args, **kw):
                args = [str(a) for a in args]
                clone_calls.append(args)
                if args[1:3] != ["repo", "clone"]:
                    forbidden_calls.append("subprocess " + " ".join(args[1:3]))
                    raise RuntimeError("selftest 只允許 gh repo clone")
                return real_subprocess.run(["git", "clone", *args[6:], origin.as_uri(), args[4]], **kw)

        feed_dir = base / "feed"
        feed_dir.mkdir()
        (feed_dir / "repo.json").write_text(json.dumps(
            [{"InternalName": "Fake", "RepoUrl": "https://github.com/ffxiv-tc-port/Fake"}]) + "\n",
            encoding="utf-8")
        (feed_dir / "release-state.json").write_text("{}\n", encoding="utf-8")
        mod.REPO_JSON = feed_dir / "repo.json"
        mod.STATE_FILE = feed_dir / "release-state.json"
        mod.ICONS_DIR = feed_dir / "icons"
        mod.SOURCE_REPOS = {"Fake": "ffxiv-tc-port/Fake"}
        mod.ICON_PATHS = {}
        mod.sync_icon = lambda *_a, **_k: False
        mod.subprocess = CloneShim
        seen = {}

        def fake_mirror_one(internal_name, source_repo, state, by_internal, report=None):
            first = mod.build_source_archive(source_repo, "v1")
            again = mod.build_source_archive(source_repo, "v1")
            with zipfile.ZipFile(first) as z:
                seen["names"] = z.namelist()
            seen.update(path=first, same=first == again, root=mod._SOURCE_ARCHIVE_DIR)
            state[internal_name] = "v1"
            return True

        mod.mirror_one = fake_mirror_one
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report = mod.main()
        parts = {p for n in seen.get("names", []) for p in n.split("/")}
        check("main() 正常結束、Fake 算成功", not report.hard_fail and report.ok == ["Fake"],
              f"ok={report.ok} fatals={report.fatals} warns={report.warns}")
        check("clone 只跑一次（同 key 第二次走快取）", len(clone_calls) == 1 and seen.get("same") is True,
              str(len(clone_calls)))
        check("clone 參數仍帶 core.longpaths=true 與 --recurse-submodules",
              bool(clone_calls) and "core.longpaths=true" in clone_calls[0]
              and "--recurse-submodules" in clone_calls[0], str(clone_calls[:1]))
        check("archive 建在 RUNNER_TEMP/msrc-* 底下",
              seen.get("root") is not None and seen["root"].parent == runner_temp
              and seen["path"].is_relative_to(seen["root"]), str(seen.get("path")))
        check("archive 有原始碼、沒有 .git／.github／.gitignore",
              "a.cs" in parts and not parts & {".git", ".github", ".gitignore"}, str(sorted(parts)))
        check("main() 結束 ⇒ archive 與暫存根目錄都不在",
              "path" in seen and not seen["path"].exists() and not msrc_dirs(runner_temp)
              and mod._SOURCE_ARCHIVE_DIR is None and not mod._source_archive_cache)

        # 5. main() 擲例外也要清，而且例外照樣往外傳。
        def boom(*_a, **_k):
            mod._get_source_archive_dir()
            raise RuntimeError("selftest boom")

        mod._run_rewrite_urls = boom
        raised = None
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                mod.main(rewrite_urls=True)
            except RuntimeError as exc:
                raised = exc
        mod._run_rewrite_urls = saved["_run_rewrite_urls"]
        check("main() 擲例外 ⇒ 例外照樣往外傳", raised is not None and "selftest boom" in str(raised))
        check("main() 擲例外 ⇒ 暫存目錄照樣清掉",
              not msrc_dirs(runner_temp) and mod._SOURCE_ARCHIVE_DIR is None)

        # 6. 清不掉時不改變結果：只多一行 [cleanup]，沒有行首 [warn]／[FAIL]。
        def locked(path):
            raise PermissionError(13, "selftest: locked", str(path))

        def only_make_dir(internal_name, source_repo, state, by_internal, report=None):
            mod._get_source_archive_dir()
            return False

        mod._rmtree_force = locked
        mod.mirror_one = only_make_dir
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report = mod.main()
        mod._rmtree_force = saved["_rmtree_force"]
        lines = out.getvalue().splitlines()
        check("清不掉 ⇒ main() 照常回傳、不算失敗", not report.hard_fail and report.ok == ["Fake"])
        check("清不掉 ⇒ 印一行 [cleanup]", any(x.startswith("[cleanup] ") for x in lines))
        check("清不掉 ⇒ 沒有行首 [warn]／[FAIL]（workflow 分級不受影響）",
              not any(x.startswith(("[warn]", "[FAIL]")) for x in lines))
    finally:
        for k, v in saved.items():
            setattr(mod, k, v)
        mod._cleanup_source_archive_dir()
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _rmtree_force(base)
    check("selftest 的暫存目錄已刪", not base.exists())
    check("沒有呼叫 gh／token／非 clone 的子行程", forbidden_calls == [], str(forbidden_calls))
    check("repo 裡的 repo.json／release-state.json 沒被動到",
          all(digest(p) == h for p, h in real_digests.items()))
    print(f"SELFTEST {'PASS' if not fails else 'FAIL'}（{len(fails)} 項失敗）")
    return 0 if not fails else 2


def _parse_argv(argv):
    """極簡手寫解析：位置參數是外掛名，`--url-shape`/`--dry-run`/`--rewrite-urls`
    是旗標。不用 argparse 是因為外掛名清單長度不定、也可能剛好撞到 `-` 開頭的
    東西不值得——這裡只認明確的旗標，其餘一律當外掛名。"""
    names = []
    url_shape = DEFAULT_URL_SHAPE
    dry_run = False
    rewrite_urls = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--url-shape":
            i += 1
            if i >= len(argv):
                print("--url-shape 缺參數（browser 或 asset-api）", file=sys.stderr)
                sys.exit(2)
            url_shape = argv[i]
        elif a.startswith("--url-shape="):
            url_shape = a.split("=", 1)[1]
        elif a == "--dry-run":
            dry_run = True
        elif a == "--rewrite-urls":
            rewrite_urls = True
        else:
            names.append(a)
        i += 1
    if url_shape not in ("browser", "asset-api"):
        print(f"未知的 --url-shape {url_shape!r}（要 browser 或 asset-api）", file=sys.stderr)
        sys.exit(2)
    return names, url_shape, dry_run, rewrite_urls


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())
    _names, _url_shape_arg, _dry_run_arg, _rewrite_urls_arg = _parse_argv(sys.argv[1:])
    _report = main(only=_names or None, url_shape=_url_shape_arg, dry_run=_dry_run_arg,
                   rewrite_urls=_rewrite_urls_arg)
    # 🔴 離開碼在這裡、而且只在這裡決定。0 = 成功（可能帶 warn），2 = 硬失敗。
    # workflow 靠這個離開碼決定要不要繼續走到 commit + push。
    sys.exit(2 if _report.hard_fail else 0)
