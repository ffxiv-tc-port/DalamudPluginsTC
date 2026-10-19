#!/usr/bin/env python3
"""Local release automation: 推 tag + 觸發外掛 repo 的 release.yml，然後就結束。

    python3 scripts/release_plugin.py --all
    python3 scripts/release_plugin.py EurekaHelper Accountant
    python3 scripts/release_plugin.py --all --dry-run

**本機不再等 CI，也不再自己 mirror。** 誰把建好的 release 同步進 repo.json？
本 repo 的排程 workflow `.github/workflows/feed-mirror.yml`（每 15 分鐘）。

    本機這支腳本          →  tag + push + dispatch  →  結束（秒級）
    外掛 repo release.yml →  建置 + 建 release
    本 repo 排程 workflow  →  mirror_releases.py → 有 diff 就 commit + push

2026-08-03 之前這支腳本是同步的：dispatch 之後就地 `wait_for_release_run`
（timeout 900s）+ `wait_for_release_visible`，再跑 `mirror.main()`。一個外掛
60~190 秒，一輪十幾個版本就是半小時的空窗，而那半小時純粹是在等 GitHub。

⚠️ `--wait` 保留舊行為（等 CI + 本機 mirror + commit + push），當作排程 workflow
壞掉時的退路。差別是它現在**會 commit 並推送 feed** —— 舊版只改檔案不 commit，
而「版本上線了但使用者的 feed 看不到」是零徵兆的失敗（2026-08-03 差點出事）。
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import mirror_releases as mirror
from app_token import get_installation_token

# 🔴 別讓「印一行字」有能力中止發版：主控台是 cp950 時，子行程輸出解碼失敗留下的 U+FFFD 與 emoji
# 編不出來會擲 UnicodeEncodeError，中止點可能落在 mirror 已經改完檔案、commit 還沒做之間。
# errors="replace" 讓這些字變成 "?" 而不是例外。
# ⚠️ 這裡只改 errors 不改 encoding：改成 utf-8 會讓中文在 cp950 主控台變成亂碼。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):  # 被重導向或不支援時無所謂
        pass


def child_env():
    """給我們自己的 Python 子腳本用的環境。

    ⚠️ 子行程的 stdout 是 pipe 時，Python 用 **locale** 編碼（這台機器是 cp950），
    而呼叫端是用 utf-8 解碼 —— 中文於是變成一串 U+FFFD。實測：
        子行程送出 b'...OK\\xa1]53 \\xad\\xd3...'（cp950 的「（53 個」）
        我方 utf-8 解碼 -> '...OK\\ufffd]53 \\ufffd\\u04f1...'
    設 PYTHONIOENCODING 讓兩邊講同一種編碼，從源頭消掉替代字元。
    """
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


# 預設不允許以個人身分觸發 workflow(見下方 dispatch 處的說明)。由 --allow-personal-identity 開啟。
ALLOW_PERSONAL_IDENTITY = False

GH = mirror.GH

FLEET_ROOT = Path(r"D:\ffxiv-tc-port")

REPO_ROOT = mirror.REPO_ROOT
# 🔴 推送 feed 一律走這支（TCToolBox App，App ID 4448133）。用 `git push` 會讓
# 後續 workflow run 的 actor 變成個人帳號，而那是改寫 git 歷史碰不到的 API 欄位。
PUSH_AS_APP = FLEET_ROOT / "_rewrite" / "push_as_app.py"
COMMIT_NAME = "Claude Sonnet 5"
COMMIT_EMAIL = "noreply@anthropic.com"

# --watch 用的監看管線(段 1 資產齊全 -> 段 2 觸發 mirror -> 段 3 feed 落地)。
# 🔴 不寫死使用者名稱:工具箱在 ~/.claude/tools/(索引見該目錄的 README.md)。
# 真的搬家了就用環境變數 FLEET_WATCH_PIPELINE 指過去 —— 讓它大聲失敗,而不是靜默
# 指到一個不存在的檔然後看起來像「監看跑完了」。
WATCH_PIPELINE = Path(os.environ.get("FLEET_WATCH_PIPELINE") or
                      Path.home() / ".claude" / "tools" / "fleet" / "release_watch_pipeline.py")

# 這一輪各外掛的 tag 去向。release_one() 跑在 ThreadPoolExecutor 裡,兩張表都要上鎖。
#   DISPATCHED_TAGS: 這一輪真的觸發了一次 release 建置 —— 推了新 tag，或對「已在 HEAD 但沒有資產」的
#                    既有 tag 重新 dispatch。**--watch 只監看這一批**，tag 是腳本自己剛算／剛推的那一個，
#                    不是事後猜回來的（猜回來的版本會讓整條監看一路綠燈卻什麼都沒驗到）。
#   UNCHANGED_TAGS:  HEAD 早就發過而且資產齊全，這一輪什麼都沒做。不納入監看，但連 tag 一起印出來。
DISPATCHED_TAGS = {}
UNCHANGED_TAGS = {}
_tags_lock = threading.Lock()


def _record_tag(table, internal_name, tag):
    with _tags_lock:
        table[internal_name] = tag

# InternalNames that do NOT have a checkout of their own because they ship from
# another plugin's repo and tag. Releasing these directly is meaningless - they
# come along for free when their host repo is released (second asset pair produced
# by the host's own release.yml: Dynamis -> DynamisWithSMA, Questionable ->
# GatheringPathRenderer; see the SOURCE_REPOS comments in mirror_releases.py).
ALIAS_INTERNAL_NAMES = {"DynamisWithSMA", "GatheringPathRenderer"}

# InternalName -> local checkout, DERIVED from mirror.SOURCE_REPOS rather than
# hand-listed: every real checkout is exactly FLEET_ROOT / <repo name> (the repo
# name already encodes differences like TriadBuddy -> FFTriadBuddyDalamud). A
# hand-written table would be a third registration point; forgetting it makes newly
# onboarded plugins silently `[skip] no LOCAL_PATHS entry` on release.
LOCAL_PATHS = {
    name: str(FLEET_ROOT / src.split("/", 1)[1])
    for name, src in mirror.SOURCE_REPOS.items()
    if name not in ALIAS_INTERNAL_NAMES
}

BRANCH = "tc-7.20"
# Era floor = current TC game version: any repo whose latest tag predates it
# jumps straight to v<floor>.0.1 instead of incrementing the previous era's
# build number (7.15 -> 7.20 -> 7.25).
# 🔴 新紀元一律寫成兩位數次版號：台服 7.3 ＝ (7, 30)、7.35 ＝ (7, 35)；寫 (7, 3) 會小於 (7, 25)。
ERA_FLOOR = (7, 25)
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)\.(\d+)$")


def git(repo_path, *args, check=True, extra_config=()):
    cmd = ["git"]
    for c in extra_config:
        cmd += ["-c", c]
    cmd += ["-C", str(repo_path), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        # 別把 extraheader（含 token）漏進例外訊息裡
        safe = " ".join(a for a in args)
        raise RuntimeError(f"git -C {repo_path} {safe} failed:\n{result.stderr}")
    return result.stdout.strip()


def git_push_as_app(repo_path, *args):
    """以 TCToolBox App 的身分推送。

    🔴 為什麼一定要這樣推：`push` 觸發的 workflow run，其 `actor.login` 是由**推送當下的
    HTTP 驗證身分**決定的，跟 commit 的 author/committer 完全無關。用個人憑證推 → 每推一次
    就在 Actions 頁面留下一筆掛著使用者真名的 run（2026-08-02 實測：清乾淨之後只要有人再推
    一次就又長出來一筆，清除變成跑步機）。

    做法沿用 `_rewrite/push_as_app.py`：把 installation token 透過 `-c http.<url>.extraheader`
    只餵給這一次 git 子行程 —— **不寫 .git/config（local/global 都不動）、不留檔案痕跡**，
    使用者手動 git push 的行為完全不受影響。

    ⚠️ 拿不到 token 就**中止**而不是靜默退回個人身分（那正是 2026-08-01 那批 51 筆的成因）。
    真的要用個人身分推，明確加 --allow-personal-identity。
    """
    token = None
    try:
        token = get_installation_token()
    except Exception as exc:  # noqa: BLE001 - 取 token 失敗一律當作沒有
        say(f"[warn] 取得 App token 失敗：{exc}")

    if not token:
        if ALLOW_PERSONAL_IDENTITY:
            say("[warn] 沒有 App token，改用個人身分推送（--allow-personal-identity）")
            return git(repo_path, *args)
        raise RuntimeError(
            "拿不到 TCToolBox App installation token，中止推送。\n"
            "這是刻意的：用個人憑證推送會讓 workflow run 掛上使用者真名。\n"
            "要強制用個人身分請加 --allow-personal-identity。")

    header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return git(repo_path, *args,
               extra_config=[f"http.https://github.com/.extraheader=Authorization: Basic {header}"])


def _leak_guard_path():
    """帳號痕跡閘門 guard.py 的位置；找不到或不只一個回 None。

    工具目錄名含帳號名，而本檔會進 GitHub，所以用萬用字元找且必須恰好一個；
    搬家或要指別處就設環境變數 FLEET_LEAK_GUARD。
    """
    env = os.environ.get("FLEET_LEAK_GUARD")
    if env:
        return Path(env) if Path(env).is_file() else None
    hits = sorted((Path.home() / ".claude" / "tools" / "fleet").glob("*_purge/guard.py"))
    return hits[0] if len(hits) == 1 else None


def leak_guard_outgoing(repo_path, ref, label):
    """推 ref 之前的帳號痕跡閘門：只掃這次會讓 origin 多出來的 commit／blob／tag 物件。

    範圍＝ref 可達的 commit 扣掉 origin 現有且本機也有的頂端（與 push_as_app.py 同一套判法），
    ref 是 annotated tag 時另掃 tag 物件。needle 由 guard.py 在執行期取得，本檔不含。
    fail-closed：找不到 guard、ls-remote 或 guard 任一出錯都擲例外。
    """
    guard = _leak_guard_path()
    if guard is None:
        raise RuntimeError("找不到帳號痕跡閘門 guard.py（或不只一個），不放行；"
                           "可用環境變數 FLEET_LEAK_GUARD 指定")
    sha = git(repo_path, "rev-parse", "--verify", ref)
    commit = git(repo_path, "rev-parse", "--verify", f"{ref}^{{commit}}")
    objs = [sha] if git(repo_path, "cat-file", "-t", sha) == "tag" else []
    remote = git(repo_path, "ls-remote", "origin")
    tips = sorted({l.split("\t")[0] for l in remote.splitlines() if "\t" in l})
    have = []
    if tips:
        chk = subprocess.run(["git", "-C", str(repo_path), "cat-file", "--batch-check"],
                             input="\n".join(tips) + "\n", capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        if chk.returncode != 0:
            raise RuntimeError(f"帳號痕跡閘門：cat-file --batch-check 失敗（rc={chk.returncode}），不放行")
        have = [x[0] for x in (l.split() for l in chk.stdout.splitlines())
                if len(x) == 3 and x[1] in ("commit", "tag")]
    with tempfile.TemporaryDirectory(prefix="release_guard_") as td:
        revs_file, objs_file = Path(td) / "revs.txt", Path(td) / "objs.txt"
        revs_file.write_text("\n".join([commit] + ["^" + h for h in have]) + "\n",
                             encoding="utf-8", newline="\n")
        objs_file.write_text("".join(o + "\n" for o in objs), encoding="utf-8", newline="\n")
        g = subprocess.run([sys.executable, str(guard), "--repo", str(repo_path),
                            "--revs-file", str(revs_file), "--objects-file", str(objs_file)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=child_env())
    for line in (g.stdout + g.stderr).splitlines():
        say(f"  [{label}] {line}")
    if g.returncode == 0:
        return
    if g.returncode == 1 and any(l.startswith("BLOCK") for l in g.stdout.splitlines()):
        raise RuntimeError("帳號痕跡閘門擋下：這次要推上去的內容有真命中（位置見上列 BLOCK），"
                           "不推 tag、不發版")
    raise RuntimeError(f"帳號痕跡閘門沒有放行（guard rc={g.returncode}，見上列輸出）")


def parse_version(tag):
    m = VERSION_RE.match(tag)
    if not m:
        raise RuntimeError(f"tag {tag!r} doesn't match the vMAJOR.MINOR.PATCH.BUILD scheme")
    return tuple(int(x) for x in m.groups())


def next_tag(internal_name, latest_tag):
    if latest_tag is None:
        return f"v{ERA_FLOOR[0]}.{ERA_FLOOR[1]}.0.1"
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
    exactly that state).

    🔴 2026-08-26 起**不能**用 `--merged HEAD` 濾上游 tag:全艦隊 squash 整理後,
    我方既有 release tag 全都不是新 HEAD 的祖先,--merged 會把它們整批濾光,
    next_tag 退化成 v7.20.0.1(當天實踩:AR 算出 .34、Mappy 算出 .1)。
    改用「紀元 scheme」判我方 tag:v 前綴 + 4 段 + (major,minor) >= (7,15)——
    我方 tag 一律是 TC 遊戲版本開頭(7.15/7.20/...),上游外掛自己的版號
    (AutoHook v6.x、Questionable v15.277.7.0)都不落在這個窗裡。"""
    out = git(repo_path, "tag")
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
        # Only tags from our own era scheme count: same major as ERA_FLOOR and
        # (major, minor) >= (7, 15), i.e. TC game-patch numbers. Upstream repos ship 4-part
        # v-tags with unrelated versions that would version-sort above ours (e.g.
        # Questionable's upstream v15.277.7.0). Without --merged (see docstring) this
        # window IS the filter.
        if ver[0] != ERA_FLOOR[0] or (ver[0], ver[1]) < (7, 15):
            continue
        versions.append((ver, line))
    if not versions:
        return None
    versions.sort()
    return versions[-1][1]


# 版號下限的來源：feed 的 scripts/release-state.json（mirror 每鏡像一版就寫入該版的 tag）。
_feed_state_cache = []
_feed_state_lock = threading.Lock()
# 只給 --dry-run 的演練用：當成本機與遠端都沒有任何版號 tag。
ASSUME_NO_TAGS = False


def fetch_remote_feed_state():
    """遠端 main 上的 release-state.json；讀不到或不是 JSON 物件回 None。"""
    out = mirror.gh("api", "-H", "Accept: application/vnd.github.raw",
                    f"repos/{mirror.PUBLIC_REPO}/contents/scripts/release-state.json?ref=main",
                    check=False)
    try:
        data = json.loads(out) if out else None
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def load_local_feed_state():
    """本機 checkout 的 release-state.json（可能落後遠端一輪）；讀不到回 None。"""
    try:
        data = json.loads(mirror.STATE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def feed_state():
    """回 (遠端, 本機) 兩份狀態，每個行程只讀一次；讀不到的那份是 None。"""
    with _feed_state_lock:
        if not _feed_state_cache:
            _feed_state_cache.append((fetch_remote_feed_state(), load_local_feed_state()))
        return _feed_state_cache[0]


def _era_version(tag):
    """我方紀元的 v 開頭四段版號回版本 tuple，其餘回 None。"""
    m = VERSION_RE.match(tag or "")
    if not m or not tag.startswith("v"):
        return None
    ver = tuple(int(x) for x in m.groups())
    return ver if ver[0] == ERA_FLOOR[0] and (ver[0], ver[1]) >= (7, 15) else None


def feed_floor_tag(internal_name):
    """回 (feed 已發到的最高版號 tag 或 None, 遠端狀態有沒有讀到)。

    同一個來源 repo 的別名（Dynamis／DynamisWithSMA）共用 tag，一起取最高。
    """
    remote, local = feed_state()
    src = mirror.SOURCE_REPOS.get(internal_name)
    names = [n for n, r in mirror.SOURCE_REPOS.items() if r == src] or [internal_name]
    tags = [st[n] for st in (remote, local) if st for n in names if _era_version(st.get(n))]
    return (max(tags, key=_era_version) if tags else None), remote is not None


def latest_known_tag(internal_name, repo_path):
    """回 (下一版要比它高的 tag, 本機 git tag, feed 下限)。

    本機 tag 被刪光或落後 feed 時由 feed 下限接手；這時遠端狀態檔讀不到就擲例外，
    不靠可能過期的本機副本，更不退回紀元的 .0.1（低於使用者已裝版本＝永不提示更新）。
    """
    local = None if ASSUME_NO_TAGS else latest_git_tag(repo_path)
    floor, remote_ok = feed_floor_tag(internal_name)
    if floor is None:
        return local, local, None
    if local is not None and parse_version(local) >= parse_version(floor):
        return local, local, floor
    if not remote_ok:
        raise RuntimeError(
            f"{internal_name}: 本機沒有不低於 feed 記錄（{floor}）的版號 tag，而遠端的 "
            f"release-state.json 讀不到 —— 不用可能過期的本機副本決定版號，請排除後重跑")
    return floor, local, floor


def era_floor_problem(floor=None):
    """ERA_FLOOR 寫法有問題時回說明字串，否則 None。次版號 1～9 一律當成把 7.x 少寫一位數。"""
    floor = ERA_FLOOR if floor is None else floor
    if 0 < floor[1] < 10:
        return (f"ERA_FLOOR={floor} 的次版號只有一位數：台服 {floor[0]}.{floor[1]} 要寫成 "
                f"({floor[0]}, {floor[1] * 10})，否則 Dalamud 會判定 v{floor[0]}.{floor[1]}.x.y 比 7.25 舊")
    return None


def _four_part(value):
    """'v7.25.0.3'／'7.25.0.3' 回版本 tuple，其餘回 None。不看紀元：比的是 Dalamud 拿來比的數字。"""
    m = VERSION_RE.match(value) if isinstance(value, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def fetch_remote_repo_json():
    """遠端 main 上的 repo.json；讀不到或不是 JSON 陣列回 None。"""
    out = mirror.gh("api", "-H", "Accept: application/vnd.github.raw",
                    f"repos/{mirror.PUBLIC_REPO}/contents/repo.json?ref=main", check=False)
    try:
        data = json.loads(out) if out else None
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def load_local_repo_json():
    try:
        data = json.loads(mirror.REPO_JSON.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


_feed_manifest_cache = []
_feed_manifest_lock = threading.Lock()


def feed_manifests():
    """回 (遠端, 本機) 兩份 repo.json，每個行程只讀一次；讀不到的那份是 None。"""
    with _feed_manifest_lock:
        if not _feed_manifest_cache:
            remote, local = fetch_remote_repo_json(), load_local_repo_json()
            if remote is None:
                say("[warn] 遠端 repo.json 讀不到：防降版只比對 release-state.json 與本機 repo.json")
            _feed_manifest_cache.append((remote, local))
        return _feed_manifest_cache[0]


def feed_current_version(internal_name):
    """回 (feed 現行最高版本 tuple, 出處) 或 (None, None)。來源＝release-state.json 與
    repo.json 的 AssemblyVersion（遠端與本機都看），同來源 repo 的別名一起取最高。"""
    src = mirror.SOURCE_REPOS.get(internal_name)
    names = {internal_name} | ({n for n, r in mirror.SOURCE_REPOS.items() if r == src} if src else set())
    found = []
    for where, st in zip(("遠端", "本機"), feed_state()):
        for n in sorted(names):
            v = _four_part((st or {}).get(n))
            if v:
                found.append((v, f"release-state.json（{where}）{n}={st[n]}"))
    for where, man in zip(("遠端", "本機"), feed_manifests()):
        for e in man or ():
            if isinstance(e, dict) and e.get("InternalName") in names:
                v = _four_part(e.get("AssemblyVersion"))
                if v:
                    found.append((v, f"repo.json（{where}）{e['InternalName']} AssemblyVersion={e['AssemblyVersion']}"))
    return max(found) if found else (None, None)


def version_guard(internal_name, tag, forced):
    """要推新 tag 前的防呆；擋下回原因字串，放行回 None。"""
    ver = parse_version(tag)
    if not forced and ver[:2] != ERA_FLOOR:
        return (f"自動算出的 {tag} 不在 ERA_FLOOR {ERA_FLOOR} 紀元（既有 tag／feed 已在 {ver[:2]}）："
                f"ERA_FLOOR 寫錯或過期，新紀元要寫兩位數次版號，例 (7, 30)")
    cur, where = feed_current_version(internal_name)
    if cur is not None and ver <= cur:
        return (f"{tag} 不高於 feed 現行版本 {'.'.join(map(str, cur))}（{where}）："
                f"Dalamud 會判定比已安裝的舊，使用者永遠收不到這一版")
    return None


# 被 version_guard 或「--tag 不高於既有 tag」擋下的插件；有任何一筆時 main() 以 exit 3 結束。
REFUSED = {}


def _refuse(internal_name, reason):
    _record_tag(REFUSED, internal_name, reason)
    say(f"[REFUSED] {internal_name}: {reason}")
    return False


def tag_points_at_head(repo_path, tag):
    """True if the given tag already exists on origin and points at the
    same commit as the local branch tip."""
    if ASSUME_NO_TAGS:
        return False
    remote_sha = git(repo_path, "ls-remote", "origin", f"refs/tags/{tag}", check=False)
    if not remote_sha:
        return False
    remote_sha = remote_sha.split()[0]
    head_sha = git(repo_path, "rev-parse", "HEAD")
    return remote_sha == head_sha


def release_has_assets(source_repo, tag):
    """True if a GitHub release exists for the tag AND carries at least one
    asset. A tag alone proves nothing: release_one pushes the tag BEFORE the
    CI run, so a failed/timed-out build leaves the tag in place with no
    release behind it. Judging "already released" by tag existence alone then
    permanently skips that plugin on every rerun (the tag matches HEAD, so it
    looks done) - the only escape used to be manually deleting the tag."""
    result = subprocess.run(
        [GH, "release", "view", tag, "--repo", source_repo,
         "--json", "assets", "-q", ".assets | length"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return False
    try:
        return int(result.stdout.strip()) > 0
    except ValueError:
        return False


def release_definitely_missing(source_repo, tag):
    """來源 repo 確定沒有這個 tag 的 release（API 明確回 404）才回 True；release 在就回 False。

    查不出來（限流、斷線）擲例外：這個答案決定要不要多打一個版號，不能拿暫時性錯誤當「沒有」。
    """
    result = subprocess.run(
        [GH, "api", f"repos/{source_repo}/releases/tags/{tag}", "--jq", ".id"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode == 0:
        return False
    if "HTTP 404" in (result.stderr or ""):
        return True
    raise RuntimeError(f"查不出 {source_repo} 的 release {tag} 在不在（{(result.stderr or '').strip()[:160]}），"
                       f"不決定版號，請重跑")


def already_released(repo_path, source_repo, tag):
    """True only if the tag points at HEAD AND its release build actually
    succeeded (a release with assets exists)."""
    return tag_points_at_head(repo_path, tag) and release_has_assets(source_repo, tag)


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
    # 用 GitHub App 的 installation token 觸發，run 就會顯示 TCToolBox[bot] 而不是
    # 操作者本人（org 本身不能當 actor，只能是使用者或 App）。
    # 🔴 拿不到 token 預設拒絕，不靜默退回個人憑證：workflow run 的 actor 是改寫 git 歷史碰不到的
    # API 欄位。要用個人身分發版必須明確加 --allow-personal-identity。
    env = None
    app_token = get_installation_token()
    if app_token:
        env = {**os.environ, "GH_TOKEN": app_token}
    elif not ALLOW_PERSONAL_IDENTITY:
        raise RuntimeError(
            f"拿不到 TCToolBox App 的 installation token，拒絕以個人身分發版 {source_repo}。\n"
            f"  workflow run 的 actor 會變成你本人，而那是清不掉的公開足跡。\n"
            f"  修法：確認 TCTOOLBOX_APP_ID / TCTOOLBOX_APP_KEY 已設定"
            f"（app_token.py 會自動從 Windows User 層級登錄檔讀，不必重開 shell）。\n"
            f"  真的要用個人身分：加上 --allow-personal-identity。"
        )

    result = None
    for attempt in range(1, retries + 1):
        result = subprocess.run(
            [GH, "workflow", "run", "release.yml", "--repo", source_repo, "--ref", tag],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        if result.returncode == 0:
            return
        if attempt < retries:
            time.sleep(delay_s)
    raise RuntimeError(f"gh workflow run release.yml --repo {source_repo} --ref {tag} "
                        f"failed after {retries} attempts:\n{result.stderr}")


# Progress reporting: every line is flushed immediately and prefixed with elapsed
# time, so a release that spends minutes inside wait_for_release_run() is visibly
# alive rather than looking like a hang.
_print_lock = threading.Lock()
_RUN_START = time.time()


def say(msg):
    with _print_lock:
        print(f"[{time.time() - _RUN_START:6.1f}s] {msg}", flush=True)


def _poll_release_run(source_repo, tag):
    """One lookup of the dispatched run. Returns (found, status, conclusion, url)."""
    out = subprocess.run(
        [GH, "run", "list", "--repo", source_repo, "--workflow=release.yml",
         "--limit", "5", "--json",
         "databaseId,headBranch,event,status,conclusion,displayTitle,url"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0 or not (out.stdout or "").strip():
        return False, None, None, None
    for r in json.loads(out.stdout):
        if r["event"] == "workflow_dispatch" and r["headBranch"] == tag:
            return True, r["status"], r["conclusion"], r.get("url")
    return False, None, None, None


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
    name = source_repo.split("/")[-1]
    deadline = time.time() + timeout_s
    started = time.time()
    seen = False
    announced_url = False
    last_status = None
    next_heartbeat = started + 30

    while time.time() < deadline:
        found, status, conclusion, url = _poll_release_run(source_repo, tag)
        seen = seen or found

        if found and not announced_url and url:
            say(f"  {name} {tag}: run started -> {url}")
            announced_url = True
        if found and status != last_status:
            say(f"  {name} {tag}: {status}")
            last_status = status
        elif time.time() >= next_heartbeat:
            # Say something even when nothing changed, so a long build is
            # visibly alive rather than looking like a hang.
            elapsed = int(time.time() - started)
            say(f"  {name} {tag}: still {last_status or 'queued'} ({elapsed}s elapsed)")
            next_heartbeat = time.time() + 30

        if found and status == "completed":
            return status, conclusion
        time.sleep(poll_s)

    # One last look - the loop can exit with a run that finished during the
    # final sleep, and reporting FAIL for a successful release is worse than
    # waiting one more round-trip.
    found, status, conclusion, _ = _poll_release_run(source_repo, tag)
    if found and status == "completed":
        return status, conclusion
    return ("timeout", None) if not (seen or found) else ("timeout", "unknown")


def release_one(internal_name, source_repo, dry_run=False, wait=False, forced_tag=None):
    """推 tag + 觸發 release.yml。

    wait=False（預設）: dispatch 完就返回，CI 跑多久與本機無關 —— 之後由本 repo 的
    排程 workflow 把 release 同步進 repo.json。
    wait=True: 舊行為，就地等 CI 跑完並確認 release 真的查得到。
    """
    repo_path = LOCAL_PATHS.get(internal_name)
    if repo_path is None:
        say(f"[skip] {internal_name}: no LOCAL_PATHS entry")
        return False
    if not Path(repo_path).exists():
        say(f"[skip] {internal_name}: {repo_path} does not exist")
        return False

    if has_uncommitted_changes(repo_path):
        say(f"[skip] {internal_name}: uncommitted changes in {repo_path}, commit or stash first")
        return False

    latest_tag, local_tag, floor_tag = latest_known_tag(internal_name, repo_path)
    if floor_tag is not None and latest_tag != local_tag:
        say(f"[{internal_name}] 版號下限取自 feed 狀態檔：{floor_tag}（本機 tag：{local_tag or '無'}）")

    # --tag 指定的版號與 HEAD 上既有 tag 不同時，即使 HEAD 早已發過也要再打一顆：
    # 「全艦隊版號一起前進」正是這個旗標存在的目的。
    redispatch = False
    if latest_tag is not None and tag_points_at_head(repo_path, latest_tag) \
            and (forced_tag is None or forced_tag == latest_tag):
        if release_has_assets(source_repo, latest_tag):
            say(f"[skip] {internal_name}: HEAD already released as {latest_tag}, nothing new to tag")
            _record_tag(UNCHANGED_TAGS, internal_name, latest_tag)
            return True
        # feed 已經發過這一版而來源 repo 的 release 不在了：同版號重建出來 mirror 會判
        # up to date、不收進 feed，HEAD 的內容永遠不出貨 ⇒ 改打下一版。
        if (floor_tag is not None and parse_version(floor_tag) >= parse_version(latest_tag)
                and release_definitely_missing(source_repo, latest_tag)):
            say(f"[{internal_name}] {latest_tag} 在 HEAD 但來源 repo 沒有 release，而 feed 已發過這一版 —— "
                f"不重 dispatch 同版號，改打下一版")
        else:
            redispatch = True
    if redispatch:
        # Tag exists at HEAD but its release build never succeeded (failed or
        # timed-out CI after the tag push). Re-dispatch the SAME tag instead of
        # cutting a duplicate - judging by tag existence alone would skip it here forever.
        say(f"[{internal_name}] {latest_tag} exists at HEAD but has no release assets - re-dispatching CI")
        if dry_run:
            # dry-run 不會真的 dispatch,但把「將會用的 tag」記下來,--watch-dry-run
            # 才印得出對組表(真正要不要執行監看由 main() 單點把關,見 run_watch)。
            _record_tag(DISPATCHED_TAGS, internal_name, latest_tag)
            return True
        tag = latest_tag
        dispatch_release_run(source_repo, tag)
        say(f"[{internal_name}] re-dispatched release.yml for {tag}")
    else:
        tag = next_tag(internal_name, latest_tag)
        if forced_tag is not None:
            if latest_tag is not None and parse_version(forced_tag) <= parse_version(latest_tag):
                return _refuse(internal_name, f"--tag {forced_tag} 不高於既有最新 tag {latest_tag}（版號只能前進）")
            tag = forced_tag
        reason = version_guard(internal_name, tag, forced=forced_tag is not None)
        if reason:
            return _refuse(internal_name, reason)
        say(f"[{internal_name}] next tag: {tag}")
        if dry_run:
            _record_tag(DISPATCHED_TAGS, internal_name, tag)
            return True

        git(repo_path, "tag", tag)
        try:
            leak_guard_outgoing(repo_path, f"refs/tags/{tag}", internal_name)
        except Exception:
            # 沒推出去的本機 tag 會被 latest_git_tag 讀到、下一輪就跳號，擋下時收回。
            git(repo_path, "tag", "-d", tag, check=False)
            raise
        git_push_as_app(repo_path, "push", "origin", tag)
        dispatch_release_run(source_repo, tag)
        say(f"[{internal_name}] pushed {tag}, dispatched release.yml")

    # 記在 dispatch **之後**:dispatch_release_run() 失敗會拋例外,那時這個外掛不該
    # 進監看名單(它的 release 根本沒被觸發,監看只會等到逾時)。
    _record_tag(DISPATCHED_TAGS, internal_name, tag)

    if not wait:
        # 🔑 本機的工作到此為止。CI 建置要 5~7 分鐘，等它是純粹的空轉；
        # repo.json 由本 repo 的排程 workflow 回寫（見模組 docstring）。
        say(f"[ok] {internal_name}: {tag} 已觸發 -> "
            f"https://github.com/{source_repo}/actions")
        return True

    say(f"[{internal_name}] waiting for CI...")
    status, conclusion = wait_for_release_run(source_repo, tag)
    if status != "completed" or conclusion != "success":
        say(f"[FAIL] {internal_name}: release.yml {status}/{conclusion} - check https://github.com/{source_repo}/actions")
        return False

    if not wait_for_release_visible(source_repo, tag):
        say(f"[FAIL] {internal_name}: {tag} 的 release 在 CI 完成後仍查不到 - "
            f"check https://github.com/{source_repo}/releases")
        return False

    say(f"[ok] {internal_name}: {tag} released")
    return True


def wait_for_release_visible(source_repo, tag, timeout_s=120, interval_s=5):
    """CI run 回報 completed 之後，release 還要幾秒才查得到。

    2026-07-31 實測：Artisan v7.20.0.37 的 run 在 12:12:5x 就 completed，
    但 release 的 publishedAt 是 12:13:06 —— mirror 在那 10 秒的空窗裡跑，
    於是把「上一版」(v7.20.0.36) 寫進 repo.json，外掛清單拿到的是舊版而且
    完全沒有錯誤訊息。等到 release 真的查得到再回報成功。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        # ⚠️ 顯式 encoding="utf-8"：text=True 單獨用會拿 locale 解碼（cp950），gh 送出的是 UTF-8，
        # 遇到解不開的位元組就 UnicodeDecodeError —— 而這裡只是在等 release 出現，不該有能力讓整輪發版中止。
        # 用 GH 常數而不是裸字串 "gh"：gh 不在 PATH 時才找得到。
        proc = subprocess.run(
            [GH, "release", "view", tag, "--repo", source_repo, "--json", "tagName"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode == 0:
            return True
        time.sleep(interval_s)
    return False


# mirror 會改寫的、且只有 mirror 會改寫的檔案。失敗還原時只碰這幾個，
# 才不會把別的 agent 同時在做的事一起還原掉。
FEED_FILES = ("repo.json", "scripts/release-state.json")


def _restore_feed_files(snapshot):
    """把 repo.json / release-state.json 還原成 mirror 之前的位元組，並清空索引。

    🔴 為什麼一定要還原：這個 repo 有一個排程 workflow 每 15 分鐘也在寫同一批檔案。
    留下「檔案已改、commit 未做」的中間狀態，下一次 `git pull` 就會跟 CI 的 commit
    撞在一起；而且**留在索引裡的變更會讓其他 agent 的 `git pull --rebase` 直接失敗**
    （"cannot pull with rebase: Your index contains uncommitted changes"）。

    這麼做是安全的，因為 mirror 的產出是**可重算的** —— 排程 workflow 會從 GitHub
    的 release 重新推導出一模一樣的 repo.json。丟掉本機這份不會損失任何資訊。
    """
    for rel, data in snapshot.items():
        path = REPO_ROOT / rel
        try:
            if data is None:
                if path.exists():
                    path.unlink()
            elif path.read_bytes() != data:
                path.write_bytes(data)
        except OSError as exc:
            say(f"[warn] 還原 {rel} 失敗：{exc}")
    # 只 unstage 我們自己 add 的路徑，別動別人可能已經 stage 的東西。
    git(REPO_ROOT, "reset", "-q", "--", *FEED_FILES, "icons", check=False)
    say("[feed] 已把 repo.json / release-state.json 還原成 mirror 之前的狀態，索引已清空。")
    say("[feed] 這一輪的同步交給排程 workflow（它會從 release 重新推導，結果一樣）。")


def _run_child(script_name):
    """跑我們自己的 Python 子腳本，回傳 (returncode, stdout, stderr)。"""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / script_name)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=child_env(),
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def mirror_and_publish(scope):
    """--wait 的退路：跑 mirror，然後把 feed commit + push 出去。**全有或全無。**

    平常這件事由 .github/workflows/feed-mirror.yml 在 CI 做。只有排程 workflow
    壞掉、需要人工頂上時才會走到這裡。

    🔴 為什麼一定要 commit + push：mirror 只改本機檔案。少了這一步的結果是
    「release 建好了、repo.json 在本機是對的，但遠端的 feed 沒動」—— 使用者的
    外掛清單看不到新版，而本機看起來一切正常，**零徵兆**。

    🔴 為什麼快照要在 mirror **之前**拍：第一版把快照放在「mirror 之後、commit
    之前」，於是還原只是把檔案還原成**已經被 mirror 改過**的樣子 —— 等於沒還原。
    測出來才發現（還原後位元組比對不相等）。而且 mirror 自己是**逐外掛增量寫檔**的，
    它跑到一半失敗同樣會留下半套的 repo.json，一起被這個快照涵蓋。
    """
    # ⚠️ 偵測而不是上鎖。feed 檔案在進來時就已經髒掉，只可能是「另一輪 mirror 正在進行」或
    # 「上一輪死在半路」——兩種情況繼續做下去都會把對方的結果混進我們的 commit。
    # 真正的跨行程鎖在這裡不划算：排程 workflow 跑在自己的 runner 上，本機的鎖檔
    # 對它無效，而 origin/main 的原子性 git 本來就保證了（非快進會被拒，下面有重試）。
    dirty = git(REPO_ROOT, "status", "--porcelain", "--", *FEED_FILES, check=False)
    if dirty:
        say(f"[FAIL] 開始前 feed 檔案就已經有未提交的變更：\n{dirty}")
        say("       可能是另一輪 mirror 正在跑，或上一輪死在半路。")
        say("       先確認那份變更該不該留（`git diff repo.json`），再重跑。")
        return False

    snapshot = {}
    for rel in FEED_FILES:
        path = REPO_ROOT / rel
        snapshot[rel] = path.read_bytes() if path.exists() else None

    try:
        n = len(scope) if scope else len(mirror.SOURCE_REPOS)
        say(f"mirroring {n} plugin(s) into repo.json...")
        mirror.main(only=scope)
        return _publish_feed_inner()
    except Exception as exc:  # noqa: BLE001 - 這裡要攔的就是「任何」失敗
        say(f"[FAIL] feed 發佈失敗：{exc!r}")
        # 失敗可能落在兩個位置，處置不同：
        #   commit 之前 -> 檔案髒但沒進歷史，還原成進來時的樣子。
        #   commit 之後(push 失敗) -> 工作樹本來就乾淨，**不要** reset，那會丟掉
        #                              一個正確的 commit；留著讓人決定怎麼推。
        ahead = git(REPO_ROOT, "rev-list", "--count", "origin/main..HEAD", check=False)
        if ahead not in ("", "0"):
            say(f"[feed] 已經 commit 但沒推出去（本地領先 origin/main {ahead} 筆），"
                f"工作樹是乾淨的。要嘛手動 push_as_app 重推，要嘛就讓排程 workflow "
                f"自己同步一次（內容會一樣），再把這筆本地 commit drop 掉。")
        else:
            _restore_feed_files(snapshot)
        return False


def _publish_feed_inner():
    # RepoUrl 保險：跟 CI 走同一支腳本，避免兩邊的判準漂移。
    rc, out, err = _run_child("verify_repo_json.py")
    say((out or err).strip())
    if rc != 0:
        # ⚠️ raise 而不是 return False：return 會跳過外層的還原，把一份沒通過保險的
        # repo.json 留在工作樹裡 —— 正是最不該留下的東西。
        raise RuntimeError("repo.json 保險檢查沒過（見上方訊息），不 commit。")

    paths = [p for p in (*FEED_FILES, "icons") if (REPO_ROOT / p).exists()]
    git(REPO_ROOT, "config", "--local", "user.name", COMMIT_NAME)
    git(REPO_ROOT, "config", "--local", "user.email", COMMIT_EMAIL)
    git(REPO_ROOT, "add", "--", *paths)
    if not git(REPO_ROOT, "diff", "--cached", "--name-only"):
        say("[feed] 沒有變更，不需要 commit")
        return True

    rc, message, err = _run_child("feed_commit_message.py")
    if rc != 0 or not message.strip():
        raise RuntimeError(f"產生 commit 訊息失敗（rc={rc}）：{err.strip()}")
    subject = message.splitlines()[0]

    # ⚠️ 訊息走檔案，不要用 -m 接中文多行。踩過的雷：shell here-string 被當字面量，
    # commit 成功、零錯誤，主旨卻變成一個 "@"。
    msg_path = REPO_ROOT / ".git" / "FEED_COMMIT_MSG"
    msg_path.write_text(message, encoding="utf-8")
    try:
        git(REPO_ROOT, "commit", "-F", str(msg_path))
    finally:
        try:
            msg_path.unlink()
        except OSError:
            pass

    actual = git(REPO_ROOT, "log", "-1", "--format=%s")
    if actual != subject:
        raise RuntimeError(f"commit 主旨不如預期。預期 {subject!r}，實際 {actual!r}")
    say(f"[feed] committed: {actual}")

    # 這個 checkout 可能有別的 agent 同時在動，而排程 workflow 也在推同一個分支，
    # 所以推不上去是預期內的事 —— rebase 之後重試。
    for attempt in (1, 2, 3):
        _rebase_if_behind()
        proc = subprocess.run(
            [sys.executable, str(PUSH_AS_APP), str(REPO_ROOT), "origin", "main"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=child_env(),
        )
        if proc.returncode == 0:
            say(f"[feed] pushed（第 {attempt} 次嘗試）。本地最新三筆:\n"
                f"{git(REPO_ROOT, 'log', '--oneline', '-3')}")
            return True
        say(f"[feed] 第 {attempt} 次推送失敗：{((proc.stdout or '') + (proc.stderr or '')).strip()}")
    raise RuntimeError("push_as_app 連續三次失敗，feed 沒有推出去")


def _rebase_if_behind():
    git(REPO_ROOT, "fetch", "origin", "main")
    if git(REPO_ROOT, "rev-list", "--count", "HEAD..origin/main") not in ("", "0"):
        say("[feed] 落後遠端，先 rebase")
        git(REPO_ROOT, "-c", "rebase.autoStash=true", "rebase", "origin/main")


def watch_command(pairs):
    """組出監看管線的指令列。pairs = [(InternalName, tag), ...]。

    🔑 這裡**不需要**任何 InternalName 對映表,兩支腳本本來就在同一個鍵空間:
    release_plugin 的目標名在 main() 已被 mirror.SOURCE_REPOS 驗過,而
    release_watch_pipeline.py 的 Target 也是拿同一支 mirror_releases 的
    SOURCE_REPOS 去查 org/repo。逐字傳過去就對 —— 自己抄一份對照表只會漂移。

    🔴 名字**逐字**傳,絕對不要順手「修正」大小寫。feed 的 InternalName 是使用者端
    認外掛的鍵:`GatherbuddyReborn`(小寫 b,而 repo 是 GatherBuddyReborn)改一個字母
    就等於換成另一個外掛,既有使用者從此永遠收不到更新,而且全程沒有任何錯誤訊息。
    """
    return [sys.executable, str(WATCH_PIPELINE),
            *(f"{name}={tag}" for name, tag in pairs)]


def _cmdline(cmd):
    """印成可以直接貼回終端機重跑的一行。"""
    return " ".join(f'"{a}"' if " " in a else a for a in cmd)


def run_watch(results, execute):
    """--watch:全部觸發成功時,拿這一輪真的推出去的 tag 啟動監看管線。回傳 exit code。

    🔴 「任一外掛沒觸發成功就不啟動監看」是刻意的:監看管線的 exit 0 意思是「這批
    外掛的 feed 逐一相符」,名單一開始就少了幾個的話,那個 0 會變成一句半真的話 ——
    而半真的成功訊息正是這條管線存在的理由。這種時候改成把成功清單和可以直接貼的
    指令列印出來,由人決定要不要只監看那一批。
    """
    failed = sorted(n for n, v in results.items() if not v)
    with _tags_lock:
        pairs = sorted(DISPATCHED_TAGS.items())
        unchanged = sorted(UNCHANGED_TAGS.items())

    if unchanged:
        say("[watch] 這一輪沒有新版可發(HEAD 早已發佈且資產齊全),不納入監看:"
            + " ".join(f"{n}={t}" for n, t in unchanged))

    if failed:
        say(f"[watch] 有 {len(failed)} 個外掛沒有觸發成功 {failed} —— 不啟動監看。")
        if pairs:
            say("[watch] 這一輪觸發成功的是:" + " ".join(f"{n}={t}" for n, t in pairs))
            say("[watch] 確認過後要只監看這一批就跑:" + _cmdline(watch_command(pairs)))
        return 1

    if not pairs:
        say("[watch] 這一輪沒有觸發任何 release 建置,沒有東西可以監看。")
        return 0

    cmd = watch_command(pairs)
    say(f"[watch] {len(pairs)} 個外掛:" + _cmdline(cmd))
    if not execute:
        say("[watch] 只印指令、不執行"
            "(--watch-dry-run,或 --dry-run 時這些 tag 只是「將會推的」)。")
        return 0
    if not WATCH_PIPELINE.exists():
        say(f"[watch] 找不到監看管線 {WATCH_PIPELINE},不執行"
            f"(可用環境變數 FLEET_WATCH_PIPELINE 指定位置)。")
        return 1

    say("[watch] 啟動監看管線(前景執行,輸出直通)")
    proc = subprocess.run(cmd, env=child_env())
    say(f"[watch] 監看管線結束,exit={proc.returncode}"
        + ("(feed 全部落地)" if proc.returncode == 0 else "(見上方結果總表)"))
    return proc.returncode


def main():
    """_main() 跑完（含 --watch／--wait）後，只要有插件被版號防呆擋下就 exit 3。"""
    with _tags_lock:
        REFUSED.clear()
    try:
        _main()
    finally:
        with _tags_lock:
            refused = sorted(REFUSED.items())
        if refused:
            say("=" * 68)
            say(f"[REFUSED] {len(refused)} 個插件被版號防呆擋下，沒有推 tag、沒有發版：")
            for name, reason in refused:
                say(f"    {name}: {reason}")
            say("=" * 68)
    if refused:
        sys.exit(3)


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugins", nargs="*", help="InternalNames to release (default: none unless --all)")
    parser.add_argument("--all", action="store_true", help="Release every plugin in SOURCE_REPOS")
    parser.add_argument("--dry-run", action="store_true", help="Only print the tag that would be cut")
    parser.add_argument("--assume-no-tags", action="store_true",
                         help="演練用，必須搭配 --dry-run：當成本機與遠端都沒有任何版號 tag，"
                              "看下一版會算成多少（應等於 feed 現行版本＋1）。")
    parser.add_argument("--tag",
                         help="所有指定外掛一律打這個 tag（例 v7.20.1.1）而不是自動 +1。"
                              "必須高於各 repo 既有最新 tag，否則該外掛跳過並大聲印出；"
                              "HEAD 已用別的 tag 發過也照樣再打（全艦隊版號一起前進用）。")
    parser.add_argument("--skip-mirror", action="store_true",
                         help="--wait 時不要跑 mirror_releases.py（沒有 --wait 時本來就不會跑）")
    parser.add_argument("--wait", action="store_true",
                         help="退路模式：就地等 CI 跑完，然後本機 mirror + commit + push feed。"
                              "平常不需要 —— repo.json 由本 repo 的排程 workflow "
                              "(.github/workflows/feed-mirror.yml) 每 15 分鐘回寫一次。")
    parser.add_argument("--workers", type=int, default=8,
                         help="How many plugins to release in parallel (default: 8)")
    # main() 無條件讀 args.allow_personal_identity：這個旗標沒在這裡註冊，任何發版都跑不起來。
    parser.add_argument("--allow-personal-identity", action="store_true",
                         help="允許在拿不到 TCToolBox App token 時以個人身分觸發 workflow "
                              "(run 的 actor 是不可變欄位,改 git 歷史蓋不掉,平常不要用)")
    parser.add_argument("--watch", action="store_true",
                         help="全部觸發成功後,接著用這一輪推出去的 tag 前景執行監看管線"
                              "(資產齊全 -> 觸發 mirror -> 等 feed 落地)。"
                              "任一個外掛沒觸發成功就不啟動,改列出成功清單讓你自己決定。")
    parser.add_argument("--watch-dry-run", action="store_true",
                         help="只印出將要執行的監看指令列(InternalName=tag 對組)就結束,"
                              "不執行監看、不碰 token、不觸發 mirror。")
    args = parser.parse_args()

    if (args.watch or args.watch_dry_run) and args.wait:
        # --wait 自己就會在本機 mirror + commit + push feed,監看管線那三件事
        # (等資產、觸發 mirror、等 feed 落地)在它跑完時已經做完了。與其定義一套
        # 交錯順序,不如讓這個組合明確報錯。
        parser.error("--watch / --watch-dry-run 不能跟 --wait 併用:"
                     "--wait 已經在本機做完 mirror + commit + push feed 了。")

    global ALLOW_PERSONAL_IDENTITY, ASSUME_NO_TAGS
    ALLOW_PERSONAL_IDENTITY = args.allow_personal_identity
    if args.assume_no_tags and not args.dry_run:
        parser.error("--assume-no-tags 只是演練，必須搭配 --dry-run。")
    ASSUME_NO_TAGS = args.assume_no_tags

    problem = era_floor_problem()
    if problem:
        sys.exit(f"[REFUSED] {problem}；什麼都沒發。")

    if args.tag is not None:
        ver = parse_version(args.tag)
        if not args.tag.startswith("v") or (ver[0], ver[1]) < ERA_FLOOR:
            parser.error(f"--tag 必須是 v 開頭且不低於 {ERA_FLOOR} 紀元的四段版號: {args.tag!r}")

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
            pool.submit(release_one, name, mirror.SOURCE_REPOS[name],
                        dry_run=args.dry_run, wait=args.wait, forced_tag=args.tag): name
            for name in targets
        }
        done = 0
        for future in as_completed(futures):
            name = futures[future]
            done += 1
            try:
                results[name] = future.result()
            except Exception as exc:
                say(f"[FAIL] {name}: {exc}")
                results[name] = False
            say(f"--- {done}/{len(futures)} plugin(s) finished ---")

    ok = [n for n, v in results.items() if v]
    failed = [n for n, v in results.items() if not v]
    say(f"{len(ok)} succeeded, {len(failed)} failed/skipped: {failed}")

    if not args.wait:
        # 預設路徑：本機到此為止。
        say("本機工作結束。repo.json 由本 repo 的排程 workflow 回寫（每 15 分鐘）：")
        say("  https://github.com/ffxiv-tc-port/DalamudPluginsTC/actions/workflows/feed-mirror.yml")
        say("  收完之後記得 `git pull` 才會看到更新後的 repo.json / release-state.json。")
        if args.watch or args.watch_dry_run:
            # 🔴 真正執行監看的唯一閘門:--watch 且不是任何一種 dry-run。
            rc = run_watch(results,
                           execute=args.watch and not args.watch_dry_run and not args.dry_run)
            if rc:
                sys.exit(rc)
        return

    if args.dry_run or args.skip_mirror or not ok:
        return

    if args.all:
        scope = None  # full sweep; we touched everything anyway
    else:
        # Scope the mirror to what we actually released - a full sweep costs several `gh`
        # round-trips per plugin regardless of how little changed. Expand by SOURCE REPO,
        # not by name: releasing Dynamis also builds DynamisWithSMA's assets from the same
        # tag, and mirroring only "Dynamis" would leave DynamisWithSMA's feed entry stale.
        released_repos = {mirror.SOURCE_REPOS[n] for n in ok}
        scope = sorted(n for n, r in mirror.SOURCE_REPOS.items() if r in released_repos)

    mirror_and_publish(scope)


if __name__ == "__main__":
    main()
