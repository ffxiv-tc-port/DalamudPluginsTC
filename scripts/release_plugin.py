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
            return True
        # Tag exists at HEAD but its release build never succeeded (failed or
        # timed-out CI after the tag push). Re-dispatch the SAME tag instead of
        # cutting a duplicate - and instead of the old behavior, which judged
        # by tag existence alone and skipped here forever.
        say(f"[{internal_name}] {latest_tag} exists at HEAD but has no release assets - re-dispatching CI")
        if dry_run:
            return True
        tag = latest_tag
        dispatch_release_run(source_repo, tag)
        say(f"[{internal_name}] re-dispatched release.yml for {tag}")
    else:
        tag = next_tag(internal_name, latest_tag)
        say(f"[{internal_name}] next tag: {tag}")
        if dry_run:
            return True

        git(repo_path, "tag", tag)
        git_push_as_app(repo_path, "push", "origin", tag)
        dispatch_release_run(source_repo, tag)
        say(f"[{internal_name}] pushed {tag}, dispatched release.yml")

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
        proc = subprocess.run(
            ["gh", "release", "view", tag, "--repo", source_repo, "--json", "tagName"],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return True
        time.sleep(interval_s)
    return False


def publish_feed_locally():
    """--wait 的退路：本機 mirror 之後把 feed commit + push 出去。

    平常這件事由 .github/workflows/mirror-releases.yml 在 CI 做。只有排程 workflow
    壞掉、需要人工頂上時才會走到這裡。

    🔴 為什麼一定要 commit + push：mirror 只改本機檔案。少了這一步的結果是
    「release 建好了、repo.json 在本機是對的，但遠端的 feed 沒動」—— 使用者的
    外掛清單看不到新版，而本機看起來一切正常，**零徵兆**。
    """
    # RepoUrl 保險：跟 CI 走同一支腳本，避免兩邊的判準漂移。
    guard = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_repo_json.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    say((guard.stdout or guard.stderr).strip())
    if guard.returncode != 0:
        say("[FAIL] repo.json 保險檢查沒過，不 commit。")
        return False

    paths = [p for p in ("repo.json", "scripts/release-state.json", "icons")
             if (REPO_ROOT / p).exists()]
    git(REPO_ROOT, "config", "--local", "user.name", COMMIT_NAME)
    git(REPO_ROOT, "config", "--local", "user.email", COMMIT_EMAIL)
    git(REPO_ROOT, "add", "--", *paths)
    if not git(REPO_ROOT, "diff", "--cached", "--name-only"):
        say("[feed] 沒有變更，不需要 commit")
        return True

    msg = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "feed_commit_message.py")],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if msg.returncode != 0:
        say(f"[FAIL] 產生 commit 訊息失敗：{msg.stderr}")
        return False
    message = msg.stdout
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
        raise RuntimeError(f"🔴 commit 主旨不如預期。\n  預期: {subject!r}\n  實際: {actual!r}")
    say(f"[feed] committed: {actual}")

    # 這個 checkout 可能有別的 agent 同時在動（2026-08-03 實測），落後就先 rebase。
    git(REPO_ROOT, "fetch", "origin", "main")
    if git(REPO_ROOT, "rev-list", "--count", "HEAD..origin/main") not in ("", "0"):
        say("[feed] 落後遠端，先 rebase")
        git(REPO_ROOT, "-c", "rebase.autoStash=true", "rebase", "origin/main")

    proc = subprocess.run(
        [sys.executable, str(PUSH_AS_APP), str(REPO_ROOT), "origin", "main"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        say(f"[FAIL] push_as_app 回傳 {proc.returncode}:\n"
            f"{((proc.stdout or '') + (proc.stderr or '')).strip()}")
        return False
    say(f"[feed] pushed。本地最新三筆:\n{git(REPO_ROOT, 'log', '--oneline', '-3')}")
    return True


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
    args = parser.parse_args()

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

    say(f"mirroring {len(scope) if scope else len(mirror.SOURCE_REPOS)} plugin(s) into repo.json...")
    mirror.main(only=scope)
    publish_feed_locally()


if __name__ == "__main__":
    main()
