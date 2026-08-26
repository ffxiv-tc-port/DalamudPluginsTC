#!/usr/bin/env python3
"""Local release automation: 推 tag + 觸發外掛 repo 的 release.yml，然後就結束。

    python3 scripts/release_plugin.py --all
    python3 scripts/release_plugin.py EurekaHelper Accountant
    python3 scripts/release_plugin.py --all --dry-run

**本機不再等 CI，也不再自己 mirror。** 誰把建好的 release 同步進 repo.json？
本 repo 的排程 workflow `.github/workflows/mirror-releases.yml`（每 15 分鐘）。

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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import mirror_releases as mirror
from app_token import get_installation_token

# 🔴 別讓「印一行字」有能力中止發版。
#
# 實際事故（2026-08-03，--wait 發 ICE）：publish_feed_locally() 印子行程輸出時丟
# UnicodeEncodeError 而中止，**中止點在 mirror 已經改完檔案、commit 還沒做之間** ——
# feed 被改好了卻沒提交，留下髒工作樹，而且整件事包在一個看起來像「發版失敗」的例外裡。
#
# 這個 console 是 cp950。中文本身在 cp950 編得出來，編不出來的是 U+FFFD（子行程輸出
# 解碼失敗留下的替代字元）和 emoji。errors="replace" 讓這些字變成 "?" 而不是例外。
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
#   DISPATCHED_TAGS: 這一輪真的觸發了一次 release 建置 —— 推了新 tag,或對「已在 HEAD
#                    但沒有資產」的既有 tag 重新 dispatch。**--watch 只監看這一批**,
#                    而且 tag 就是腳本自己剛算/剛推的那一個,不是事後 `git describe`
#                    猜回來的(猜回來的版本會讓整條監看一路綠燈卻什麼都沒驗到)。
#   UNCHANGED_TAGS:  HEAD 早就發過而且資產齊全,這一輪什麼都沒做。不納入監看,但連
#                    tag 一起印出來 —— 「沒監看到什麼」必須看得見,不能靜默消失。
DISPATCHED_TAGS = {}
UNCHANGED_TAGS = {}
_tags_lock = threading.Lock()


def _record_tag(table, internal_name, tag):
    with _tags_lock:
        table[internal_name] = tag

# InternalNames that do NOT have a checkout of their own because they ship from
# another plugin's repo and tag. Releasing these directly is meaningless - they
# come along for free when their host repo is released.
#   DynamisWithSMA: second asset pair produced by Dynamis's own release.yml.
#   GatheringPathRenderer: second asset pair produced by Questionable's own
#   release.yml (same tag/release as Questionable itself) - see the SOURCE_REPOS
#   comment in mirror_releases.py.
ALIAS_INTERNAL_NAMES = {"DynamisWithSMA", "GatheringPathRenderer"}

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
        # Only tags from our own era scheme count: (major, minor) >= (7, 15),
        # i.e. TC game-patch numbers. Upstream repos ship 4-part v-tags with
        # unrelated versions that would version-sort above ours - e.g.
        # Questionable's upstream v15.277.7.0 (once produced a bogus
        # v15.277.7.1 release, 2026-07-28) or hypothetical upstream v7.x with
        # x < 15. Without --merged (see docstring) this window IS the filter.
        if ver[0] != ERA_FLOOR[0] or (ver[0], ver[1]) < (7, 15):
            continue
        versions.append((ver, line))
    if not versions:
        return None
    versions.sort()
    return versions[-1][1]


def tag_points_at_head(repo_path, tag):
    """True if the given tag already exists on origin and points at the
    same commit as the local branch tip."""
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
    #
    # 🔴 這裡原本是「拿不到就靜默退回個人憑證」，理由是「純加值、不該擋住發版」。
    # 那個判斷的代價在 2026-08-01 具體化了：環境變數沒被 shell 繼承 → token 拿不到 →
    # 51 次發版全部以個人身分觸發，而 workflow run 的 actor 是**改寫 git 歷史碰不到的
    # API 欄位**，等於把先前清乾淨的身分足跡又寫回去，全程零警告。
    #
    # 現在改成預設拒絕。要用個人身分發版必須明確加 --allow-personal-identity，
    # 讓它成為一個看得見的決定而不是一個沉默的預設值。
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


# Progress reporting. A release spends minutes inside wait_for_release_run() and
# used to print nothing at all between "dispatched" and the final verdict, so both
# the operator and anyone reading the transcript just saw a hang. Every line is
# flushed immediately and prefixed with elapsed time.
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


def release_one(internal_name, source_repo, dry_run=False, wait=False):
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

    latest_tag = latest_git_tag(repo_path)

    if latest_tag is not None and tag_points_at_head(repo_path, latest_tag):
        if release_has_assets(source_repo, latest_tag):
            say(f"[skip] {internal_name}: HEAD already released as {latest_tag}, nothing new to tag")
            _record_tag(UNCHANGED_TAGS, internal_name, latest_tag)
            return True
        # Tag exists at HEAD but its release build never succeeded (failed or
        # timed-out CI after the tag push). Re-dispatch the SAME tag instead of
        # cutting a duplicate - and instead of the old behavior, which judged
        # by tag existence alone and skipped here forever.
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
        say(f"[{internal_name}] next tag: {tag}")
        if dry_run:
            _record_tag(DISPATCHED_TAGS, internal_name, tag)
            return True

        git(repo_path, "tag", tag)
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
        # ⚠️ 顯式 encoding="utf-8"：text=True 單獨用會拿 runner 的 locale 解碼
        # （這台是 cp950），gh 送出的是 UTF-8，遇到解不開的位元組就 UnicodeDecodeError
        # —— 而這裡只是在等 release 出現，不該有能力讓整輪發版中止。
        # 順帶把裸字串 "gh" 換成 GH 常數，跟本檔其他呼叫一致（gh 不在 PATH 時才找得到）。
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

    平常這件事由 .github/workflows/mirror-releases.yml 在 CI 做。只有排程 workflow
    壞掉、需要人工頂上時才會走到這裡。

    🔴 為什麼一定要 commit + push：mirror 只改本機檔案。少了這一步的結果是
    「release 建好了、repo.json 在本機是對的，但遠端的 feed 沒動」—— 使用者的
    外掛清單看不到新版，而本機看起來一切正常，**零徵兆**。

    🔴 為什麼快照要在 mirror **之前**拍：第一版把快照放在「mirror 之後、commit
    之前」，於是還原只是把檔案還原成**已經被 mirror 改過**的樣子 —— 等於沒還原。
    測出來才發現（還原後位元組比對不相等）。而且 mirror 自己是**逐外掛增量寫檔**的，
    它跑到一半失敗同樣會留下半套的 repo.json，一起被這個快照涵蓋。
    """
    # ⚠️ 偵測而不是上鎖。這個 checkout 今天實際發生過兩個 agent 交錯提交，而
    # feed 檔案在進來時就已經髒掉，只可能是「另一輪 mirror 正在進行」或「上一輪
    # 死在半路」——兩種情況繼續做下去都會把對方的結果混進我們的 commit。
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugins", nargs="*", help="InternalNames to release (default: none unless --all)")
    parser.add_argument("--all", action="store_true", help="Release every plugin in SOURCE_REPOS")
    parser.add_argument("--dry-run", action="store_true", help="Only print the tag that would be cut")
    parser.add_argument("--skip-mirror", action="store_true",
                         help="--wait 時不要跑 mirror_releases.py（沒有 --wait 時本來就不會跑）")
    parser.add_argument("--wait", action="store_true",
                         help="退路模式：就地等 CI 跑完，然後本機 mirror + commit + push feed。"
                              "平常不需要 —— repo.json 由本 repo 的排程 workflow "
                              "(.github/workflows/mirror-releases.yml) 每 15 分鐘回寫一次。")
    parser.add_argument("--workers", type=int, default=8,
                         help="How many plugins to release in parallel (default: 8)")
    # 這個旗標的檢查邏輯在 0ed7a65 就加好了,但當時漏了在這裡註冊,
    # 導致 main() 一開頭就 AttributeError —— 也就是任何發版都跑不起來。
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

    global ALLOW_PERSONAL_IDENTITY
    ALLOW_PERSONAL_IDENTITY = args.allow_personal_identity

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
                        dry_run=args.dry_run, wait=args.wait): name
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
        say("  https://github.com/ffxiv-tc-port/DalamudPluginsTC/actions/workflows/mirror-releases.yml")
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
        # Scope the mirror to what we actually released - a full sweep is ~156
        # `gh` round-trips regardless of how little changed.
        #
        # Expand by SOURCE REPO, not by name: releasing Dynamis also builds
        # DynamisWithSMA's assets from the same tag, and mirroring only
        # "Dynamis" would leave DynamisWithSMA's feed entry pointing at the
        # previous release.
        released_repos = {mirror.SOURCE_REPOS[n] for n in ok}
        scope = sorted(n for n, r in mirror.SOURCE_REPOS.items() if r in released_repos)

    mirror_and_publish(scope)


if __name__ == "__main__":
    main()
