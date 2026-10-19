#!/usr/bin/env python3
"""mirror_releases.py 逐外掛失敗分級的離線測試。

    python scripts/test_mirror_grading.py

不碰網路、不呼叫 `gh`、不動 repo 裡真正的 repo.json —— 每個情境都在自己的暫存
目錄裡跑，`mirror_one` / `sync_icon` 用假的替身。

為什麼需要這支：分級規則的失敗形式**全都是靜默的** ——「半套資料被寫進 feed」
與「該硬失敗卻回綠燈」在 CI 上都長得像成功。這裡把四條規則各釘一個案例：

  warn-continue   一個外掛炸掉 → 其餘照常更新，炸掉那個沿用 feed 既有條目
  hard ①          全部炸掉     → 硬失敗，離開碼 2
  hard ②          feed 寫不進去 → 硬失敗
  hard ③          炸掉且沒有既有條目可沿用 → 硬失敗
"""
import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import mirror_releases as m  # noqa: E402

PATCHED = ("REPO_JSON", "STATE_FILE", "ICONS_DIR", "SOURCE_REPOS", "ICON_PATHS",
           "mirror_one", "sync_icon")

OLD_VER = "7.20.0.1"
NEW_VER = "7.20.0.2"


def make_entry(name, version=OLD_VER):
    return {
        "InternalName": name,
        "AssemblyVersion": version,
        "RepoUrl": f"https://github.com/ffxiv-tc-port/{name}",
        "DownloadLinkInstall": f"https://example/{name}-{version}.zip",
        "DownloadLinkUpdate": f"https://example/{name}-{version}.zip",
    }


def fake_mirror_factory(explode):
    """回傳一個假的 mirror_one。`explode` 裡的外掛會先把條目改到一半再爆炸 ——
    這正是真實情況：mirror_one 是就地改 entry 的，資產上傳成功、changelog 抓失敗
    這種順序會留下「版本號跳了但下載連結還是舊的」的半套條目。"""

    def fake(internal_name, source_repo, state, by_internal, report=None):
        entry = by_internal.get(internal_name)
        if entry is not None:
            entry["AssemblyVersion"] = NEW_VER          # 半套：先改版本
        if internal_name in explode:
            raise RuntimeError(
                f"gh api repos/{source_repo}/releases failed:\n"
                "gh: Server Error (HTTP 502)\nretry later\n")
        if entry is not None:
            url = f"https://example/{internal_name}-{NEW_VER}.zip"
            entry["DownloadLinkInstall"] = url          # 半套的另一半
            entry["DownloadLinkUpdate"] = url
        state[internal_name] = NEW_VER
        return True

    return fake


def run_case(names, explode=(), entries=None, state=None, break_state_file=False):
    """跑一輪 main()，回傳 (report, 磁碟上的 repo.json, 磁碟上的 state, summary, stdout)。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mirror-grade-test-"))
    saved = {k: getattr(m, k) for k in PATCHED}
    summary_path = tmp / "step-summary.md"
    env_saved = {k: os.environ.get(k) for k in ("GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS")}
    try:
        feed = entries if entries is not None else [make_entry(n) for n in names]
        (tmp / "repo.json").write_text(json.dumps(feed, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
        state_file = (tmp / "no-such-dir" / "release-state.json") if break_state_file \
            else (tmp / "release-state.json")
        if not break_state_file:
            state_file.write_text(json.dumps(state or {}, indent=2) + "\n", encoding="utf-8")

        m.REPO_JSON = tmp / "repo.json"
        m.STATE_FILE = state_file
        m.ICONS_DIR = tmp / "icons"
        m.SOURCE_REPOS = {n: f"ffxiv-tc-port/{n}" for n in names}
        m.ICON_PATHS = {}                       # 圖示同步整個跳過（另有案例單測）
        m.mirror_one = fake_mirror_factory(set(explode))

        os.environ["GITHUB_STEP_SUMMARY"] = str(summary_path)
        os.environ["GITHUB_ACTIONS"] = "true"

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            report = m.main()

        on_disk = json.loads((tmp / "repo.json").read_text(encoding="utf-8"))
        on_disk_state = json.loads(state_file.read_text(encoding="utf-8")) \
            if state_file.exists() else None
        summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
        return report, {e["InternalName"]: e for e in on_disk}, on_disk_state, summary, buf.getvalue()
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
        for k, v in env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def exit_code_of(report):
    """跑檔案裡真正的 __main__ 區塊（不是複製一份判斷式），確認離開碼對得上。"""
    src = m.__file__ and pathlib.Path(m.__file__).read_text(encoding="utf-8")
    tail = src.split('if __name__ == "__main__":')[1]
    body = "\n".join(line[4:] if line.startswith("    ") else line
                     for line in tail.splitlines())
    scope = dict(vars(m))
    # 直接餵已經跑好的 report；只有 __main__ 的呼叫簽章要對得上，其餘不重要。
    scope["main"] = lambda only=None, url_shape=None, dry_run=None, rewrite_urls=None: report
    try:
        exec(compile(body, "mirror_releases.__main__", "exec"), scope)
    except SystemExit as exc:
        return exc.code
    return 0


FAILURES = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


# ── 案例 1：一個外掛炸掉 → warn，其餘照常，炸掉那個沿用既有條目 ──────────
print("[case 1] 單一外掛失敗 → [warn] + 沿用既有條目")
report, feed, state, summary, out = run_case(["Alpha", "Bravo", "Charlie"], explode=["Bravo"])
check("離開碼 0", exit_code_of(report) == 0)
check("hard_fail=False", report.hard_fail is False)
check("1 筆 warn", len(report.warns) == 1, str(report.warns))
check("warn 種類是 mirror/Bravo", report.warns[0][:2] == ("mirror", "Bravo"))
check("其餘兩個進 ok", sorted(report.ok) == ["Alpha", "Charlie"])
check("Alpha 更新到新版", feed["Alpha"]["AssemblyVersion"] == NEW_VER)
check("Bravo 沿用舊版本", feed["Bravo"]["AssemblyVersion"] == OLD_VER,
      feed["Bravo"]["AssemblyVersion"])
check("Bravo 沒有半套資料（版本與下載連結一致）",
      OLD_VER in feed["Bravo"]["DownloadLinkInstall"])
check("Bravo 沒有被記進 release-state", "Bravo" not in state, str(state))
check("Alpha/Charlie 有被記進 release-state", state.get("Alpha") == NEW_VER)
check("warn 訊息被壓成單行（gh 的多行 stderr）",
      all(len(line.split("gh: Server Error")) < 3 for line in out.splitlines())
      and any(l.startswith("[warn] mirror Bravo:") and "retry later" in l
              for l in out.splitlines()))
check("stdout 沒有任何行首 [FAIL]",
      not any(l.startswith("[FAIL]") for l in out.splitlines()))
check("summary 開頭是 ⚠️ 計數", summary.splitlines()[0].startswith("## ⚠️ 有 1 筆警告"),
      summary.splitlines()[0])
check("summary 列出外掛名與原因", "| Bravo | mirror |" in summary)
check("有輸出 ::warning:: annotation",
      any(l.startswith("::warning title=mirror mirror Bravo::") for l in out.splitlines()))
check("annotation 沒有裸換行", all("\n" not in l for l in out.splitlines()))

# ── 案例 2：全綠 ────────────────────────────────────────────────────
print("[case 2] 全部成功 → 全綠，summary 一眼可分")
report, feed, state, summary, out = run_case(["Alpha", "Bravo"])
check("離開碼 0", exit_code_of(report) == 0)
check("0 warn 0 fatal", not report.warns and not report.fatals)
check("summary 是 ✅", summary.startswith("## ✅ mirror 全綠（2 個外掛，0 警告）"),
      summary.strip())
check("沒有 ::warning:: annotation", "::warning" not in out)

# ── 案例 3：硬失敗 ① 全部外掛都失敗 ──────────────────────────────────
print("[case 3] 全部外掛都失敗 → 硬失敗（平台級）")
report, feed, state, summary, out = run_case(["Alpha", "Bravo"], explode=["Alpha", "Bravo"])
check("離開碼 2", exit_code_of(report) == 2)
check("hard_fail=True", report.hard_fail is True)
check("fatal 種類含 all-failed", any(k == "all-failed" for k, _, _ in report.fatals),
      str(report.fatals))
check("兩個外掛都沿用舊條目",
      feed["Alpha"]["AssemblyVersion"] == OLD_VER and feed["Bravo"]["AssemblyVersion"] == OLD_VER)
check("summary 是 🔴", summary.startswith("## 🔴 mirror 硬失敗"), summary.splitlines()[0])
check("stdout 有行首 [FAIL]（workflow 的保險抓得到）",
      any(l.startswith("[FAIL]") for l in out.splitlines()))

# ── 案例 4：硬失敗 ③ 失敗且沒有既有條目可沿用 ────────────────────────
print("[case 4] 失敗且 repo.json 沒有既有條目 → 硬失敗（新外掛首發不能靜默漏）")
report, feed, state, summary, out = run_case(
    ["Alpha", "Newbie"], explode=["Newbie"], entries=[make_entry("Alpha")])
check("離開碼 2", exit_code_of(report) == 2)
check("fatal 種類是 no-feed-entry",
      [k for k, _, _ in report.fatals] == ["no-feed-entry"], str(report.fatals))
check("Alpha 仍然更新成功（沒有被一起擋掉）", feed["Alpha"]["AssemblyVersion"] == NEW_VER)
check("all-failed 沒有誤觸發", not any(k == "all-failed" for k, _, _ in report.fatals))

# ── 案例 5：硬失敗 ② feed 寫入失敗 ──────────────────────────────────
print("[case 5] feed 檔案寫不進去 → 硬失敗")
report, feed, state, summary, out = run_case(["Alpha", "Bravo"], break_state_file=True)
check("離開碼 2", exit_code_of(report) == 2)
check("fatal 種類是 persist", {k for k, _, _ in report.fatals} == {"persist"},
      str(report.fatals))
check("summary 是 🔴", summary.startswith("## 🔴 mirror 硬失敗"))

# ── 案例 6：圖示失敗只是 warn，而且不算「這個外掛失敗」──────────────
print("[case 6] 圖示同步失敗 → warn，不觸發 all-failed")


def _boom_icon(internal_name, source_repo, entry, report=None):
    raise RuntimeError("icon fetch exploded")


tmp = pathlib.Path(tempfile.mkdtemp(prefix="mirror-grade-icon-"))
saved = {k: getattr(m, k) for k in PATCHED}
try:
    (tmp / "repo.json").write_text(json.dumps([make_entry("Alpha")], indent=2) + "\n",
                                   encoding="utf-8")
    (tmp / "release-state.json").write_text("{}\n", encoding="utf-8")
    m.REPO_JSON, m.STATE_FILE, m.ICONS_DIR = tmp / "repo.json", tmp / "release-state.json", tmp / "icons"
    m.SOURCE_REPOS = {"Alpha": "ffxiv-tc-port/Alpha"}
    m.ICON_PATHS = {"Alpha": "images/icon.png"}
    m.sync_icon = _boom_icon
    m.mirror_one = fake_mirror_factory(set())
    os.environ.pop("GITHUB_STEP_SUMMARY", None)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report = m.main()
    check("hard_fail=False", report.hard_fail is False)
    check("1 筆 icon warn", [k for k, _, _ in report.warns] == ["icon"], str(report.warns))
    check("外掛本身仍算成功", report.ok == ["Alpha"])
    check("圖示失敗不算進 failed_plugins（不會誤判平台事故）", report.failed_plugins == set())
finally:
    for k, v in saved.items():
        setattr(m, k, v)
    shutil.rmtree(tmp, ignore_errors=True)

# ── 案例 7：Changelog 濾掉發版流程 commit（兩向）──────────────────────
# 🔴 這組案例要釘的是**兩個方向**：漏濾只是噪音，誤濾是使用者看不到真的改了什麼。
print("[case 7] Changelog 排除規則：流程主旨濾掉、功能主旨原樣保留")

_SRC = pathlib.Path(m.__file__).read_text(encoding="utf-8")

FEATURE_A = "優先任務清單可以一鍵補上整串還沒做的前置任務，不必自己一個一個找出來排"
FEATURE_B = "更正按窗診斷旁一句與實測不符的註解：按壓頻率其實不低"
# 下面兩條逐字取自 repo.json 實際出現過的 Changelog
PROCESS_1 = "重掛後觸發 GitHub Actions 重新註冊發版工作流程"
PROCESS_2 = "重掛後觸發 GitHub Actions 重新註冊兩個工作流程"
# fork_standalone_rebuild.py 的重建收尾產生的那一顆
PROCESS_3 = "重掛後觸發 GitHub Actions 重新註冊工作流程"
NL = chr(10)


def changelog_of(subjects):
    """直接餵 get_changelog 一份「compare API 已經取好第一行」的清單。"""
    saved_gh = m.gh
    m.gh = lambda *a, **k: NL.join(subjects)
    try:
        return m.get_changelog("ffxiv-tc-port/Fake", "v1", "v2")
    finally:
        m.gh = saved_gh


WANT = "- " + FEATURE_A + NL + "- " + FEATURE_B

cl = changelog_of([PROCESS_1, FEATURE_A, FEATURE_B])
check("流程主旨被濾掉", cl is not None and PROCESS_1 not in cl, cl)
check("功能主旨原樣保留、順序不變", cl == WANT, cl)

for label, subj in (("發版工作流程", PROCESS_1), ("兩個工作流程", PROCESS_2),
                    ("工作流程", PROCESS_3)):
    check(f"三種實際出現過的流程主旨都命中規則：{label}", m.is_process_commit(subj) is True)

check("不含流程主旨時逐字原樣（負對照）", changelog_of([FEATURE_A, FEATURE_B]) == WANT)
check("真功能主旨不誤濾：BossmodReborn e9352ee3b 改版號機制那顆",
      m.is_process_commit(
          "Adopt VersionPrefix + BuildNumber.txt versioning to match other repos,"
          " add gate job") is False)
check("真功能主旨不誤濾：提到 GitHub Actions 但不是重新註冊",
      m.is_process_commit("修好 GitHub Actions 建置失敗：缺 DalamudLibPath") is False)
check("全部都是流程 commit → 回 None（退回「無 changelog」的既有路徑）",
      changelog_of([PROCESS_1, PROCESS_3]) is None)
check("回 None 時不會寫出空字串（呼叫端是 `if changelog:`）",
      "changelog = get_changelog(source_repo, prev_tag, tag)" in _SRC
      and "if changelog:" in _SRC)


# ── 案例 8：--rewrite-urls 只重寫 URL 欄位，其他欄位（含 RepoUrl）不動 ──────
print("[case 8] --rewrite-urls：只改 DownloadLinkInstall/Update")


def _fake_gh_rewrite(*args, **kwargs):
    """假 gh()：模擬 `repos/<PUBLIC_REPO>/releases/tags/<tag>` 的 --jq 輸出。
    不打網路、不認得真的 repo，純靠 tag 字串分派。"""
    joined = " ".join(args)
    if "Alpha-v1.0" in joined:
        return json.dumps([{"name": "Alpha.zip", "id": 111},
                            {"name": "Alpha-source.zip", "id": 112}])
    if "Bravo-v1.0" in joined:
        return json.dumps([])  # 沒有資產：release 還沒鏡像過／tag 對不上
    if "Charlie-v1.0" in joined:
        return json.dumps([{"name": "Charlie-source.zip", "id": 113}])  # 只有 source zip
    return ""


def run_rewrite_case(entries, state, url_shape="asset-api", dry_run=True):
    saved_gh, saved_repo = m.gh, m.PUBLIC_REPO
    try:
        m.gh = _fake_gh_rewrite
        m.PUBLIC_REPO = "ffxiv-tc-port/DalamudPluginsTC"
        by_internal = {e["InternalName"]: e for e in entries}
        report = m.MirrorReport(len(entries))
        persisted = []

        def fake_persist(also_repo_json):
            persisted.append(also_repo_json)

        targets = {e["InternalName"]: "ffxiv-tc-port/" + e["InternalName"] for e in entries}
        m._run_rewrite_urls(targets, by_internal, state, url_shape, dry_run, report, fake_persist)
        return report, by_internal, persisted
    finally:
        m.gh, m.PUBLIC_REPO = saved_gh, saved_repo


def browser_entry(name):
    e = make_entry(name, OLD_VER)
    url = f"https://github.com/ffxiv-tc-port/DalamudPluginsTC/releases/download/{name}-v1.0/{name}.zip"
    e["DownloadLinkInstall"] = url
    e["DownloadLinkUpdate"] = url
    return e


entries8 = [browser_entry("Alpha"), browser_entry("Bravo"), browser_entry("Charlie")]
state8 = {"Alpha": "v1.0", "Bravo": "v1.0", "Charlie": "v1.0"}

report8, by8, persisted8 = run_rewrite_case([dict(e) for e in entries8], dict(state8), dry_run=True)
check("dry-run：Alpha 一個字元都沒被寫（只印 before/after）",
      by8["Alpha"]["DownloadLinkInstall"] == entries8[0]["DownloadLinkInstall"])
check("dry-run：完全沒呼叫 persist()", persisted8 == [])
check("Alpha 算出新 URL → 記進 ok", "Alpha" in report8.ok)
check("Bravo 沒有資產 → rewrite-lookup fatal 且不進 ok",
      any(k == "rewrite-lookup" and n == "Bravo" for k, n, _ in report8.fatals)
      and "Bravo" not in report8.ok)
check("Charlie 只有 source.zip 沒有主 zip → rewrite-lookup fatal",
      any(k == "rewrite-lookup" and n == "Charlie" for k, n, _ in report8.fatals))
check("hard_fail=True（Bravo/Charlie 都 fatal）", report8.hard_fail is True)

report8b, by8b, persisted8b = run_rewrite_case([dict(entries8[0])], {"Alpha": "v1.0"}, dry_run=False)
check("真寫：Alpha 的 DownloadLinkInstall 換成 asset-api 形狀",
      by8b["Alpha"]["DownloadLinkInstall"]
      == "https://api.github.com/repos/ffxiv-tc-port/DalamudPluginsTC/releases/assets/111")
check("真寫：DownloadLinkUpdate 跟著換、其餘欄位（含 RepoUrl/AssemblyVersion）一個字元不動",
      by8b["Alpha"]["DownloadLinkUpdate"] == by8b["Alpha"]["DownloadLinkInstall"]
      and by8b["Alpha"]["RepoUrl"] == entries8[0]["RepoUrl"]
      and by8b["Alpha"]["AssemblyVersion"] == entries8[0]["AssemblyVersion"])
check("真寫：真的呼叫了 persist(True)", persisted8b == [True])

report8c, by8c, persisted8c = run_rewrite_case(
    [dict(entries8[0])], {"Alpha": "v1.0"}, url_shape="browser", dry_run=True)
check("已經是目標形狀（browser）→ 不改、記進 ok、不呼叫 persist",
      by8c["Alpha"]["DownloadLinkInstall"] == entries8[0]["DownloadLinkInstall"]
      and "Alpha" in report8c.ok and persisted8c == [])

report8d, by8d, _ = run_rewrite_case([dict(entries8[0])], {}, dry_run=True)  # 沒有 state
check("release-state.json 沒有記錄過的 tag → rewrite-skip warn，entry 不動",
      any(k == "rewrite-skip" and n == "Alpha" for k, n, _ in report8d.warns)
      and by8d["Alpha"]["DownloadLinkInstall"] == entries8[0]["DownloadLinkInstall"])


# ---- 9. dry-run 挑的 .zip 與正式路徑同一顆（共用 release 的插件只看自己的資產） ----
def run_dry_pick(internal_name, asset_names):
    """用真的 mirror_one 走到 dry-run 分支，回傳它拿去算 URL 的資產名。"""
    saved = {k: getattr(m, k) for k in
             ("latest_release", "gh", "resolve_download_url", "_dry_run")}
    picked = []
    try:
        m.latest_release = lambda repo: {"tag": "v9.9.9.9",
                                         "assets": [{"name": n} for n in asset_names]}
        m.gh = lambda *a, **k: "[]"  # feed 上已有這顆 release、但沒有 source 資產 ⇒ 不會被判 up to date
        m.resolve_download_url = \
            lambda repo, tag, name, shape, report, internal: picked.append(name)
        m._dry_run = True
        with contextlib.redirect_stdout(io.StringIO()):
            m.mirror_one(internal_name, "ffxiv-tc-port/Dynamis", {}, {})
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
    return picked


SHARED_ASSETS = ["Dynamis.zip", "Dynamis.json", "DynamisWithSMA.zip", "DynamisWithSMA.json"]
check("dry-run：共用 release 的第二個插件挑自己的 .zip",
      run_dry_pick("DynamisWithSMA", SHARED_ASSETS) == ["DynamisWithSMA.zip"],
      str(run_dry_pick("DynamisWithSMA", SHARED_ASSETS)))
check("dry-run：資產名對不上 InternalName 時退回第一顆 .zip",
      run_dry_pick("Other", ["latest.zip", "x.json"]) == ["latest.zip"])


# ---- 10. 出貨包自帶 Newtonsoft.Json.dll：不在已知名單的記 warn（不算外掛失敗），已知的只印 note ----
def run_bundle_note(internal_name, zip_members):
    import zipfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mirror-bundle-test-"))
    try:
        z = tmp / "latest.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for member in zip_members:
                zf.writestr(member, b"x")
        (tmp / f"{internal_name}.json").write_text("{}", encoding="utf-8")
        report = m.MirrorReport(targets=1)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            hit = m.note_bundled_newtonsoft(internal_name, [z, tmp / f"{internal_name}.json"], report)
        return hit, report, buf.getvalue()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


hit10a, report10a, out10a = run_bundle_note("Alpha", ["Alpha.dll", "Newtonsoft.Json.dll"])
check("自帶且不在已知名單 → 記一筆 bundled-newtonsoft warn",
      hit10a and [(k, n) for k, n, _ in report10a.warns] == [("bundled-newtonsoft", "Alpha")], out10a)
check("那筆 warn 不算外掛失敗、不是硬失敗",
      report10a.failed_plugins == set() and not report10a.hard_fail)
hit10b, report10b, out10b = run_bundle_note("Alpha", ["Alpha/alpha.dll", "Alpha/newtonsoft.json.DLL"])
check("子目錄＋大小寫不同也認得", hit10b and len(report10b.warns) == 1)
hit10c, report10c, out10c = run_bundle_note("Mappy", ["Mappy.dll", "Newtonsoft.Json.dll"])
check("已知名單 → 只印 [note]、不記 warn",
      hit10c and report10c.warns == [] and "[note] bundled-newtonsoft Mappy" in out10c, out10c)
hit10d, report10d, out10d = run_bundle_note("Alpha", ["Alpha.dll", "Newtonsoft.Json.Bson.dll", "MyNewtonsoft.Json.dll.txt"])
check("沒有自帶（名字只是相似）→ 什麼都不記", not hit10d and report10d.warns == [] and out10d == "", out10d)

print("")
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("all checks passed")
