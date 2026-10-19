#!/usr/bin/env python3
"""mirror_releases.py 的 Changelog 來源（compare／changelog-overrides.json／失敗時的警告）離線測試。

    python scripts/test_mirror_changelog.py

gh、下載、source archive、帳號痕跡閘門全部換成替身，不碰 GitHub，也不寫 repo 裡的 feed 檔。
最後一段真的跑 mirror_one()，確認 resolve_changelog 在鏡像流程裡會被呼叫到。
"""
import io
import json
import pathlib
import sys
import tempfile
from contextlib import redirect_stdout

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import mirror_releases as m  # noqa: E402

NL = chr(10)
FAILURES = []
SRC, PREV, TAG = "example-invalid/Fake", "v7.25.0.4", "v7.25.0.5"
FALLBACK = ["相依函式庫對齊、整理註解（功能不變）"]


def check(label, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


def fake_gh(subjects=None, status=""):
    """compare 的兩種查詢：取主旨清單（subjects=None 代表 API 失敗）與取 .status。"""
    def gh(*args, **kwargs):
        if ".status" in args:
            return status
        return NL.join(subjects) if subjects else ""
    return gh


def resolve(tmp, overrides, subjects, status="", prev=PREV, name="Fake"):
    """寫一份 overrides（None＝沒有檔）→ get_changelog → resolve_changelog；回 (結果, 輸出, report)。"""
    path = tmp / "changelog-overrides.json"
    if path.exists():
        path.unlink()
    if overrides is not None:
        path.write_text(overrides if isinstance(overrides, str) else json.dumps(overrides, ensure_ascii=False), encoding="utf-8")
    m.CHANGELOG_OVERRIDES_FILE = path
    m._changelog_overrides_cache.clear()
    m._changelog_notes.clear()
    m.gh = fake_gh(subjects, status)
    report, out = m.MirrorReport(1), io.StringIO()
    with redirect_stdout(out):
        cl = m.get_changelog(SRC, prev, TAG)
        cl = m.resolve_changelog(name, SRC, prev, TAG, cl, report)
    return cl, out.getvalue(), report


def real_gh_failure_shape():
    """真的 gh() 包裝遇到 HTTP 錯誤的形狀：`gh api` 把錯誤 JSON 印到 stdout、離開碼非 0。

    上面的替身把「API 失敗」模擬成空字串，分不出這個形狀；這裡換掉的是 subprocess，走真的 m.gh。
    """
    class Done:
        returncode = 1
        stdout = '{"message":"Not Found","documentation_url":"https://example.invalid/doc","status":"404"}'
        stderr = "gh: Not Found (HTTP 404)"

    saved_run, saved_env = m.subprocess.run, m._gh_env
    m.subprocess.run = lambda *a, **k: Done()
    m._gh_env = lambda: None
    m._changelog_notes.clear()
    try:
        check("gh api 回 404（stdout 有錯誤 JSON）＋check=False ⇒ 回空字串", m.gh("api", "repos/x/compare/a...b", check=False) == "")
        out = io.StringIO()
        with redirect_stdout(out):
            cl = m.get_changelog(SRC, PREV, TAG)
        check("同一形狀 ⇒ get_changelog 回 None 並記成 compare-failed（不會把錯誤 JSON 當成 commit 主旨）",
              cl is None and m._changelog_notes.get((SRC, PREV, TAG)) == "compare-failed", repr(cl))
        try:
            m.gh("api", "repos/x/compare/a...b")
            raised = False
        except RuntimeError:
            raised = True
        check("check=True ⇒ 照舊擲例外", raised)
    finally:
        m.subprocess.run, m._gh_env = saved_run, saved_env
        m._changelog_notes.clear()


def main():
    saved = {k: getattr(m, k) for k in ("gh", "CHANGELOG_OVERRIDES_FILE", "latest_release", "download_asset",
                                        "build_source_archive", "resolve_download_url")}
    saved_guard = m.leak_guard.check_files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        try:
            print("情境 1  沒有覆寫檔（現況）")
            cl, out, rep = resolve(tmp, None, ["修好甲", "新增乙"])
            check("compare 成功 ⇒ 逐字是 compare 的結果、沒有警告", cl == "- 修好甲" + NL + "- 新增乙" and not rep.warns, repr(cl))
            cl, out, rep = resolve(tmp, None, None, prev=None)
            check("沒有上一版（首次鏡像）⇒ None、沒有警告", cl is None and not rep.warns)
            cl, out, rep = resolve(tmp, None, None, status="identical")
            check("比得出來但中間沒有 commit ⇒ None、沒有警告", cl is None and not rep.warns, str(rep.warns))
            cl, out, rep = resolve(tmp, None, None)
            check("compare 失敗 ⇒ 仍回 None（沿用舊 Changelog），但印出一筆 [warn] changelog",
                  cl is None and len(rep.warns) == 1 and rep.warns[0][0] == "changelog" and "[warn] changelog Fake" in out
                  and f"{PREV}...{TAG}" in out, out)
            check("這筆警告不算插件層級失敗（不會讓整輪被判成全部失敗）", "Fake" not in rep.failed_plugins and not rep.hard_fail)

            print("情境 2  只有共用文字")
            ov = {"fallback_when_compare_fails": FALLBACK}
            cl, out, rep = resolve(tmp, ov, None)
            check("compare 失敗 ⇒ 用共用文字、不警告、輸出說明來源", cl == "- " + FALLBACK[0] and not rep.warns and "共用文字" in out, out)
            cl, out, rep = resolve(tmp, ov, ["修好甲"])
            check("compare 成功 ⇒ 共用文字不介入", cl == "- 修好甲")
            cl, out, rep = resolve(tmp, ov, None, status="identical")
            check("沒有 commit（不是失敗）⇒ 共用文字不介入", cl is None and not rep.warns)

            print("情境 3  插件專屬條目")
            ov = {"fallback_when_compare_fails": FALLBACK, "plugins": {"Fake": {"tag": TAG, "lines": ["更新咬鉤時間資料", "相依函式庫對齊"]}}}
            cl, out, rep = resolve(tmp, ov, None)
            check("tag 相符、compare 失敗 ⇒ 用專屬那幾行", cl == "- 更新咬鉤時間資料" + NL + "- 相依函式庫對齊" and not rep.warns, repr(cl))
            cl, out, rep = resolve(tmp, ov, ["compare 來的"])
            check("tag 相符、compare 成功 ⇒ 仍以專屬條目為準", cl.startswith("- 更新咬鉤時間資料"))
            cl, out, rep = resolve(tmp, ov, ["別人的"], name="Other")
            check("別的插件不受影響", cl == "- 別人的")
            ov["plugins"]["Fake"]["tag"] = "v7.25.0.9"
            cl, out, rep = resolve(tmp, ov, ["compare 來的"])
            check("tag 不符、compare 成功 ⇒ 不套用、用 compare、只印說明不警告", cl == "- compare 來的" and not rep.warns and "不套用" in out, out)
            cl, out, rep = resolve(tmp, ov, None)
            check("tag 不符、compare 失敗 ⇒ 用共用文字，並警告專屬那幾行沒用上",
                  cl == "- " + FALLBACK[0] and len(rep.warns) == 1 and "沒有用上" in rep.warns[0][2], str(rep.warns))

            print("情境 4  覆寫檔壞掉")
            for label, bad in (("不是 JSON", "{oops"), ("lines 是空的", {"plugins": {"Fake": {"tag": TAG, "lines": []}}}),
                               ("行裡有換行", {"fallback_when_compare_fails": ["a" + NL + "b"]})):
                cl, out, rep = resolve(tmp, bad, ["修好甲"])
                check(f"{label} ⇒ 警告一次、整份不套用、compare 結果照常", cl == "- 修好甲" and len(rep.warns) == 1 and "不套用" in rep.warns[0][2], str(rep.warns))

            print("情境 5  壓縮 commit 的主旨不進 Changelog")
            check("主旨命中流程規則", m.is_process_commit("台服移植：彙整歷史為單一 commit") is True)
            check("相近的真功能主旨不誤濾", m.is_process_commit("台服移植：補上任務彙整頁") is False)
            cl, out, rep = resolve(tmp, None, ["台服移植：彙整歷史為單一 commit", "修好甲"])
            check("compare 帶到那一顆時被濾掉", cl == "- 修好甲", repr(cl))

            print("情境 5b  整理註解的 commit 不進 Changelog")
            tidy1, tidy2 = "整理註解：設定頁與 IPC 端點（只動註解）", "整理註解：戰鬥模組（只動註解）"
            aligned = "相依函式庫對齊到同一版（功能不變）"
            check("主旨以「整理註解」開頭 ⇒ 命中", m.is_process_commit(tidy1) is True)
            check("「相依函式庫對齊到同一版（功能不變）」不濾", m.is_process_commit(aligned) is False)
            check("中間才出現「整理註解」的功能主旨不濾", m.is_process_commit("修好甲，順手整理註解") is False)
            cl, out, rep = resolve(tmp, None, [tidy1, "修好甲", tidy2, aligned])
            check("混合 ⇒ 只留功能那幾行、順序不變", cl == "- 修好甲" + NL + "- " + aligned, repr(cl))
            cl, out, rep = resolve(tmp, None, [tidy1, tidy2])
            check("全是整理註解 ⇒ 固定一句「整理註解（功能不變）」，不是 None（不沿用上一版）", cl == "- 整理註解（功能不變）" and not rep.warns, repr(cl))
            check("走這條路時印得出來", "全是整理註解" in out and f"{PREV}...{TAG}" in out, out)
            cl, out, rep = resolve(tmp, None, [tidy1, "重掛後觸發 GitHub Actions 重新註冊工作流程"])
            check("整理註解＋流程 commit ⇒ 同樣是那一句", cl == "- 整理註解（功能不變）", repr(cl))
            cl, out, rep = resolve(tmp, None, ["重掛後觸發 GitHub Actions 重新註冊工作流程"])
            check("只有重新註冊那種流程 commit ⇒ 維持原行為回 None", cl is None and not rep.warns, repr(cl))

            print("情境 5c  只改 README 的 commit 不進 Changelog")
            readme = "更新 README（程式碼不變）"
            check("固定主旨 ⇒ 命中", m.is_process_commit(readme) is True)
            check("主旨多帶別的字 ⇒ 不濾", m.is_process_commit("更新 README（程式碼不變），順手修好甲") is False)
            check("「整理註解」仍是 COMMENT_ONLY_PATTERN", m.COMMENT_ONLY_PATTERN.pattern == "^整理註解")
            cl, out, rep = resolve(tmp, None, [readme, "修好甲"])
            check("混合 ⇒ 只留功能那一行", cl == "- 修好甲", repr(cl))
            cl, out, rep = resolve(tmp, None, [readme])
            check("只有 README ⇒ 維持原行為回 None（沿用上一版）", cl is None and not rep.warns, repr(cl))

            print("情境 6  呼叫鏈：真的跑 mirror_one()，上一版 tag 比不出來")
            (tmp / "changelog-overrides.json").write_text(json.dumps({"fallback_when_compare_fails": FALLBACK}, ensure_ascii=False), encoding="utf-8")
            m._changelog_overrides_cache.clear()
            m._changelog_notes.clear()
            calls = []

            def gh(*args, **kwargs):
                calls.append(args)
                return ""
            m.gh = gh
            m.latest_release = lambda repo: {"tag": TAG, "published": "2026-10-11T00:00:00Z",
                                             "assets": [{"name": "Fake.zip", "url": "u1"}, {"name": "Fake.json", "url": "u2"}]}

            def download(url, dest):
                pathlib.Path(dest).write_text(json.dumps({"AssemblyVersion": "7.25.0.5"}) if str(dest).endswith(".json") else "zip", encoding="utf-8")
            m.download_asset = download
            src_zip = tmp / "src.zip"
            src_zip.write_bytes(b"PK")
            m.build_source_archive = lambda source_repo, tag: src_zip
            m.leak_guard.check_files = lambda files: (True, "stub")
            m.resolve_download_url = lambda *a, **k: "https://example.invalid/asset/1"
            entry = {"InternalName": "Fake", "Changelog": "- 舊的 Changelog", "AssemblyVersion": "7.25.0.4"}
            state, report, out = {"Fake": PREV}, m.MirrorReport(1), io.StringIO()
            with redirect_stdout(out):
                changed = m.mirror_one("Fake", SRC, state, {"Fake": entry}, report)
            check("鏡像成功、版本與 state 前進", changed is True and state["Fake"] == TAG and entry["AssemblyVersion"] == "7.25.0.5", out.getvalue())
            check("Changelog 換成共用文字，不是沿用舊的", entry["Changelog"] == "- " + FALLBACK[0], entry["Changelog"])
            check("過程有打 compare（走的是真的那條路）", any(f"compare/{PREV}...{TAG}" in " ".join(a) for a in calls))
            (tmp / "changelog-overrides.json").unlink()
            m._changelog_overrides_cache.clear()
            m._changelog_notes.clear()
            entry = {"InternalName": "Fake", "Changelog": "- 舊的 Changelog", "AssemblyVersion": "7.25.0.4"}
            state, report, out = {"Fake": PREV}, m.MirrorReport(1), io.StringIO()
            with redirect_stdout(out):
                m.mirror_one("Fake", SRC, state, {"Fake": entry}, report)
            check("負對照：沒有覆寫檔 ⇒ 沿用舊 Changelog，而且 report 有一筆 changelog 警告",
                  entry["Changelog"] == "- 舊的 Changelog" and [w[0] for w in report.warns] == ["changelog"], str(report.warns))
        finally:
            for k, v in saved.items():
                setattr(m, k, v)
            m.leak_guard.check_files = saved_guard
            m._changelog_overrides_cache.clear()
            m._changelog_notes.clear()

    real_gh_failure_shape()

    print("")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
