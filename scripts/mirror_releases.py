#!/usr/bin/env python3
"""Poll private plugin repos for new releases and mirror their assets into
this public repo's own releases, then refresh repo.json.

Runs only inside the DalamudPluginsTC repo's own GitHub Actions workflow,
using a token that is never stored in the source plugin repos.
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
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app_token import get_installation_token

GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_JSON = REPO_ROOT / "repo.json"
STATE_FILE = REPO_ROOT / "scripts" / "release-state.json"
ICONS_DIR = REPO_ROOT / "icons"
PUBLIC_REPO = "ffxiv-tc-port/DalamudPluginsTC"

# InternalName -> source repo (owner/name)
SOURCE_REPOS = {
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
    # GatheringPathRenderer 是 Questionable repo 底下的第二個出貨 root（同一個 tag
    # 的 release.yml 一次建兩組資產），跟 Dynamis/DynamisWithSMA 完全同構——見上面
    # DynamisWithSMA 的註解。
    "GatheringPathRenderer": "ffxiv-tc-port/Questionable",
    "TextAdvance": "ffxiv-tc-port/TextAdvance",
    "Crossingway": "ffxiv-tc-port/Crossingway",
    "IINACT": "ffxiv-tc-port/IINACT",
    "TCToolbox": "ffxiv-tc-port/TCToolbox",
    "WondrousTailsSolver": "ffxiv-tc-port/EzWondrousTails",
}

# InternalName -> icon path within the source repo (on its default branch).
# raw.githubusercontent.com can't serve files from private repos anonymously,
# so we mirror the icon into this public repo instead.
ICON_PATHS = {
    "TCToolbox": "images/icon.png",
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
    # GatheringPathRenderer: 刻意不在這裡列——upstream 自己的 repo 裡從沒放過圖示檔
    # (upstream 自己的 release.yml 甚至從沒建置過這個子專案,純粹是掛在 repo 裡的
    # maintainer 用工具，manifest 的 Punchline 也自己承認是 "[Questionable dev
    # plugin]"),也沒有第三方 icon 倉庫可以借。留空不是遺漏：manifest 沒有 IconUrl
    # 時 Dalamud 有自己的 DefaultIcon 後備（PluginImageCache.DownloadPluginIconAsync
    # 在 url 是空字串時直接回 null,呼叫端會退到內建的通用外掛圖示),不是破圖。
}


_APP_TOKEN_CACHE: list = []


def _gh_env():
    """讓 `gh` 用 TCToolBox App 的 installation token，而不是本機登入的個人 PAT。

    🔴 為什麼需要這個：這個腳本是**在本機跑**的（DalamudPluginsTC 沒有
    `.github/workflows/`），所以 `gh release create` 會用本機 `gh auth` 的憑證 ——
    也就是使用者本人的 PAT。結果是這個 repo 的 474 個 release 全部
    `author.login` 都是個人帳號，而**改寫 git 歷史完全碰不到那個欄位**
    （2026-08-01 實查：51 個外掛 repo 都是 `github-actions[bot]`，只有這裡是個人帳號）。

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
    if check and result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed:\n{result.stderr}")
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
    「真的沒有 release」走的是另一條路：API 回 200 + 空陣列 → jq 得到 "null"，
    離開碼 0，照樣回 None —— 兩種情況從此可以分辨。
    """
    out = gh("api", f"repos/{repo}/releases", "--jq",
              "sort_by(.published_at) | reverse | .[0] "
              "| {tag: .tag_name, published: .published_at, "
              "assets: [.assets[] | {name, url}]}")
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


# ── 逐外掛失敗分級（2026-08-18 加） ──────────────────────────────────────
# 起因：2026-08-17 晚 GitHub 平台事故期間，**單一外掛**的暫時性 API 錯誤讓整輪
# mirror 以失敗收場（workflow 的 `grep ^[FAIL]` 一律 exit 1），其餘 50 幾個外掛
# 已經建好的 release 全部沒能寫進 feed —— 一個外掛的暫時性錯誤擋住所有人的更新。
#
# 分級規則（[warn] 繼續跑、[FAIL] 停下不寫 feed）：
#   [warn] mirror        單一外掛抓 release／資產／上傳失敗，而它在 repo.json
#                        **有**既有條目 → 把該條目與 release-state 還原成這一輪
#                        開始前的樣子（不留半套資料），其餘外掛照常處理。
#   [warn] icon          圖示同步失敗（純外觀）。
#   [warn] orphan        release 鏡像成功但 repo.json 沒有條目（既有行為，不改）。
#   [FAIL] all-failed    ① 所有外掛都失敗 —— 平台級問題，寫 feed 沒有意義。
#   [FAIL] persist       ② repo.json／release-state.json 寫入失敗（feed 本身壞了）。
#   [FAIL] no-feed-entry ③ 失敗的外掛在 repo.json 沒有既有條目可沿用 —— 新外掛
#                        首發不能靜默漏（漏掉的表現是「使用者清單裡沒這個外掛」，
#                        零徵兆）。
#   [FAIL] unexpected    分級邏輯本身有洞（不該發生 → 當硬失敗，不要吞）。
#
# 🔴 離開碼只由 __main__ 決定，不由 main() 決定：release_plugin.py 是 import 進來
# 直接呼叫 main() 的，它的行為必須跟加固前一樣。


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


def mirror_one(internal_name, source_repo, state, by_internal, report=None):
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
        # Case-INSENSITIVE stem match. GatherbuddyReborn publishes its manifest as
        # "GatherBuddyReborn.json" (capital B) while its InternalName is
        # "GatherbuddyReborn" - a one-character difference that made the manifest
        # invisible here for the plugin's entire life, so its Description /
        # Punchline / Author / DalamudApiLevel were NEVER synced (it silently fell
        # through to the parse-version-out-of-the-tag path below). Harmless so far
        # only because those fields happened to be hand-correct; the real danger is
        # DalamudApiLevel going stale across an API bump, which is the field Dalamud
        # uses to decide whether to offer the plugin at all. Found 2026-07-29.
        # Verified no two InternalNames collide when lowercased, and that
        # Dynamis/DynamisWithSMA still separate correctly.
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

        # Bundle a matching source snapshot alongside the binary, uniformly for
        # every plugin regardless of upstream license - see the 2026-07-25
        # license-audit decision (memory: project_license_audit_20260725).
        # .git is stripped (top-level + every submodule's own) so this can't
        # leak commit-author identity - the snapshot carries only what is
        # already public via every RepoUrl in repo.json.
        #
        # Only built when it's actually going to be uploaded - this is the single
        # most expensive operation in the script (a full recursive clone of the
        # tagged commit), and the "already mirrored, just refreshing repo.json"
        # path has no use for it.
        named_src_zip = None
        if not release_exists or not has_source_asset:
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
            # Deliberately do NOT record state here. Recording it would make the
            # next run see already_current==True and short-circuit to
            # "[up to date]", so the metadata would never be written even after
            # somebody adds the repo.json entry - the plugin would sit on
            # placeholder version/download links forever and the only fix would be
            # hand-editing release-state.json. Leaving state unset costs one
            # repeated asset download next run and then self-heals.
            msg = ("no repo.json entry - release mirrored, but metadata NOT synced "
                   "and state NOT recorded. Add the entry and re-run; it will pick "
                   "up from here.")
            # ⚠️ 這是**成功路徑**的警告（release 真的鏡像好了），跟分級規則 ③ 的
            # no-feed-entry 不是同一件事 —— 那個是「失敗且沒有舊條目可沿用」。
            # 這裡維持既有的 warn：升成硬失敗會讓一個少填的條目擋住其他 50 幾個
            # 外掛的 feed，正是這次加固要消滅的形狀。
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
            url = f"https://github.com/{PUBLIC_REPO}/releases/download/{public_tag}/{zip_asset}"
            entry["DownloadLinkInstall"] = url
            entry["DownloadLinkUpdate"] = url

        changelog = get_changelog(source_repo, prev_tag, tag)
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


def main(only=None):
    """Mirror releases into this repo's releases + repo.json.

    `only` restricts the run to specific InternalNames; None sweeps everything.
    A full sweep costs >= 2 `gh` calls per plugin (latest_release + release view)
    plus one per icon - ~156 round-trips across 53 plugins even when nothing has
    changed - so release_plugin.py passes just the plugins it actually released.

    回傳 MirrorReport（見上面的分級規則）。🔴 **不會**因為逐外掛失敗而拋例外、
    也**不會**自己決定離開碼 —— release_plugin.py 的 --wait 退路模式是 import
    進來直接呼叫這個函式的，它的行為必須跟加固前一樣。離開碼只在 __main__ 決定。
    """
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

    changed = False

    def persist(also_repo_json):
        # state/repo_json/by_internal are shared across threads - only the
        # persistence (and the entry mutations inside mirror_one, which all
        # happen before this point in the same call) need serializing.
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

    # Icon sync used to be a serial loop over every plugin - ~50 `gh api`
    # round-trips completed before any mirroring even started, and the single
    # slowest part of an otherwise no-op run. Each call touches a different
    # plugin's own repo.json entry and its own icons/<name>.png, so there is
    # nothing to serialize.
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

    # 分級規則 ①：全部都失敗＝平台級問題（2026-08-17 那一晚就是這個形狀，只是
    # 當時連一個外掛失敗都會擋住全部）。這時候寫 feed 沒有意義，硬失敗停下來。
    failed = report.failed_plugins
    if targets and len(failed) == len(targets):
        report.fatal("all-failed", "*",
                     f"{len(targets)} 個外掛全部失敗 —— 判定為平台級問題，這一輪不寫 feed")

    shutil.rmtree(_SOURCE_ARCHIVE_DIR, ignore_errors=True)
    scope = f"{len(targets)} plugin(s) checked"
    print(f"done ({scope})" if changed else f"no repo.json changes ({scope})")
    report.print_tail()
    report.emit_github()
    return report


if __name__ == "__main__":
    _report = main(only=sys.argv[1:] or None)
    # 🔴 離開碼在這裡、而且只在這裡決定。0 = 成功（可能帶 warn），2 = 硬失敗。
    # workflow 靠這個離開碼決定要不要繼續走到 commit + push。
    sys.exit(2 if _report.hard_fail else 0)
