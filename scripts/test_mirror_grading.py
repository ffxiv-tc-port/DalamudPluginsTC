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
    scope["main"] = lambda only=None: report          # 直接餵已經跑好的 report
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

print("")
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("all checks passed")
