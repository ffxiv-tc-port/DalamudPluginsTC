#!/usr/bin/env python3
"""repo.json 寫入之後的保險。mirror_releases.py 跑完、commit 之前呼叫。

🔴 為什麼存在：`RepoUrl` 是 Dalamud 判定「這個外掛已經安裝了」的一部分。
改掉它 —— 哪怕只是大小寫或結尾斜線 —— 全艦隊的既有安裝會跟 feed 失聯，
使用者端看到的是「外掛消失了 / 變成沒安裝過」，而 feed 本身完全正常，
沒有任何錯誤訊息。2026-07 已經踩過一次。

這支腳本很便宜（純本機、不打網路），所以在每一次寫入之後都跑一遍：
拿 `HEAD:repo.json` 當基準，比對工作樹的 repo.json。

檢查項目
--------
1. 工作樹的 repo.json 仍是合法 JSON 陣列（不帶 BOM），每個條目都有 InternalName 且不重複。
2. 舊有的每個 InternalName 都還在（mirror 不該讓條目消失）。
3. 舊有條目的 RepoUrl 一字不差。
4. 每個條目都有非空的 RepoUrl（新加的條目也要有）。
5. 條目總數沒有減少。

新增條目是允許的（新外掛上架）。

用法：
    python3 scripts/verify_repo_json.py            # 對 HEAD 比對
    python3 scripts/verify_repo_json.py <ref>      # 對指定 ref 比對
    python3 scripts/verify_repo_json.py --selftest # 離線自測（拋棄式 git repo，不碰真的 repo.json）

離開碼 0 = 沒問題；1 = 有問題（訊息印在 stderr）；--selftest 失敗是 2。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_JSON = REPO_ROOT / "repo.json"

# ⚠️ 這支腳本的訊息全是中文，而它幾乎總是被當子行程呼叫（stdout 是 pipe）。
# Windows 上 Python 對 pipe 用 **locale** 編碼（cp950），呼叫端卻用 utf-8 解碼 —— 中文會變成一串 U+FFFD，
# 呼叫端再把 U+FFFD 印到 cp950 主控台就 UnicodeEncodeError。這裡固定成 UTF-8，讓輸出跟呼叫端的解碼假設一致。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def git_show(ref: str, path: str, root=None) -> str | None:
    """讀出某個 ref 上的檔案內容；讀不到回 None（例如 repo.json 是全新檔案）。

    ⚠️ 顯式 encoding="utf-8"：repo.json 裡有大量中文，text=True 會用 runner 的
    locale 解碼（Windows 上是 cp950），直接 UnicodeDecodeError。
    """
    result = subprocess.run(
        ["git", "-C", str(root or REPO_ROOT), "show", f"{ref}:{path}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return None
    return result.stdout


def by_internal(text: str, allow_bom: bool = False) -> dict:
    """InternalName -> 條目。InternalName 重複就擲 ValueError：字典會靜默留後者，
    前一筆的 RepoUrl 被改就看不到。

    allow_bom：基準要開——不剝的話 json.loads 擲例外、落進「基準本身就壞了」分支，
    整段跳過 RepoUrl 比對還 exit 0。工作樹刻意不開：mirror_releases.py 用 utf-8
    讀 repo.json，帶 BOM 下一輪就讀不了。
    """
    if allow_bom:
        text = text.lstrip(chr(0xFEFF))
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("repo.json 的最外層不是陣列")
    out = {}
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"第 {i} 個條目不是物件")
        name = entry.get("InternalName")
        if not name:
            raise ValueError(f"第 {i} 個條目沒有 InternalName")
        if name in out:
            raise ValueError(f"InternalName 重複：{name}（第 {i} 個條目）")
        out[name] = entry
    return out


def selftest() -> int:
    """拋棄式 git repo 裡的合成 repo.json：各種改壞的方式都要 exit 1，新增條目與行尾差異要 exit 0。

    基準有兩顆：tag `plain`（無 BOM）與 HEAD（帶 BOM）。BOM 那顆要真的被比對，
    不能落進「基準本身就壞了」的略過分支。
    """
    import io
    import os
    import shutil
    import stat
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr
    fails = []
    lf, crlf = chr(10), chr(13) + chr(10)

    def check(label, got, want, detail=""):
        ok = got == want
        print(f"  [{'ok' if ok else 'FAIL'}] {label} got={got!r} want={want!r}"
              + (f"  {detail!r}" if detail and not ok else ""))
        if not ok:
            fails.append(label)

    base = tempfile.mkdtemp(prefix="verify_repojson_selftest_")
    feed = [{"InternalName": "A", "RepoUrl": "https://github.com/ffxiv-tc-port/A"},
            {"InternalName": "GatherbuddyReborn", "RepoUrl": "https://github.com/ffxiv-tc-port/GatherBuddyReborn"}]
    extra = {"InternalName": "N", "RepoUrl": "https://github.com/ffxiv-tc-port/N"}

    def git(*a):
        subprocess.run(["git", "-C", base, "-c", "user.name=selftest", "-c", "user.email=selftest@invalid", *a],
                       check=True, capture_output=True)

    def write(entries, bom=False, use_crlf=False):
        text = json.dumps(entries, ensure_ascii=False, indent=2)
        if use_crlf:
            text = text.replace(lf, crlf)
        with open(os.path.join(base, "repo.json"), "wb") as fh:
            fh.write((chr(0xFEFF) if bom else "").encode("utf-8") + text.encode("utf-8"))

    def run(*argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["x", *argv], root=base)
        return rc, out.getvalue() + err.getvalue()

    try:
        subprocess.run(["git", "init", "-q", base], check=True, capture_output=True)
        git("config", "core.autocrlf", "false")
        write(feed)
        git("add", "repo.json")
        git("commit", "-qm", "plain")
        git("tag", "plain")
        write(feed, bom=True)
        git("add", "repo.json")
        git("commit", "-qm", "bom")
        case_only = [feed[0], dict(feed[1], RepoUrl="https://github.com/ffxiv-tc-port/Gatherbuddyreborn")]
        cases = [
            ("原封不動 ⇒ 0", list(feed), {}, (), 0),
            ("新增條目 ⇒ 0", feed + [extra], {}, (), 0),
            ("行尾換成 CRLF ⇒ 0", list(feed), {"use_crlf": True}, (), 0),
            ("RepoUrl 只改大小寫 ⇒ 1（基準帶 BOM 也要比）", case_only, {}, (), 1),
            ("RepoUrl 只改大小寫 ⇒ 1（基準不帶 BOM，對照）", case_only, {}, ("plain",), 1),
            ("RepoUrl 加結尾斜線 ⇒ 1", [dict(feed[0], RepoUrl=feed[0]["RepoUrl"] + "/"), feed[1]], {}, (), 1),
            ("條目消失 ⇒ 1", [feed[0]], {}, (), 1),
            ("條目被換掉（總數不變）⇒ 1", [feed[0], extra], {}, (), 1),
            ("RepoUrl 空 ⇒ 1", feed + [dict(extra, RepoUrl=" ")], {}, (), 1),
            ("InternalName 重複（前一筆被改）⇒ 1",
             [dict(feed[0], RepoUrl="https://github.com/someone/A"), feed[0], feed[1]], {}, ("plain",), 1),
            ("工作樹帶 BOM ⇒ 1（mirror_releases 讀不了）", list(feed), {"bom": True}, (), 1),
        ]
        for label, entries, kw, args, want in cases:
            write(entries, **kw)
            rc, text = run(*args)
            check(label, rc, want, text)
        write(feed)
        rc, text = run()
        check("BOM 基準真的被比對（沒落進「基準本身就壞了」）", "本身就壞了" in text, False, text)
        check("通過時的輸出不變", text.strip(), "[verify] repo.json OK（2 個條目，RepoUrl 全數未變動）")
        with open(os.path.join(base, "repo.json"), "wb") as fh:
            fh.write(b"{ not json")
        check("工作樹不是 JSON ⇒ 1", run()[0], 1)
        write(feed)
        check("基準 ref 讀不到 ⇒ 只檢查目前內容、0", run("no-such-ref")[0], 0)
    finally:
        def onexc(func, p, _exc):
            os.chmod(p, stat.S_IWRITE)
            func(p)
        if sys.version_info >= (3, 12):
            shutil.rmtree(base, onexc=onexc)
        else:
            shutil.rmtree(base, onerror=onexc)
    check("暫存目錄已刪", os.path.exists(base), False)
    print(f"SELFTEST {'PASS' if not fails else 'FAIL'}（{len(fails)} 項失敗）")
    return 0 if not fails else 2


def main(argv: list[str], root=None) -> int:
    if "--selftest" in argv[1:]:
        return selftest()
    ref = argv[1] if len(argv) > 1 else "HEAD"
    repo_json = Path(root) / "repo.json" if root else REPO_JSON

    try:
        new = by_internal(repo_json.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"🔴 工作樹的 repo.json 讀不出來或格式不對：{exc}", file=sys.stderr)
        return 1

    problems: list[str] = []

    for name, entry in sorted(new.items()):
        if not (entry.get("RepoUrl") or "").strip():
            problems.append(f"{name}: RepoUrl 是空的")

    old_text = git_show(ref, "repo.json", root)
    if old_text is None:
        print(f"[verify] {ref}:repo.json 讀不到（首次建立？），只檢查目前內容")
    else:
        try:
            old = by_internal(old_text, allow_bom=True)
        except ValueError as exc:
            print(f"[verify] {ref}:repo.json 本身就壞了（{exc}），只檢查目前內容")
            old = {}

        for name, old_entry in sorted(old.items()):
            new_entry = new.get(name)
            if new_entry is None:
                problems.append(f"{name}: 條目整個消失了")
                continue
            if new_entry.get("RepoUrl") != old_entry.get("RepoUrl"):
                problems.append(
                    f"{name}: RepoUrl 被改了\n"
                    f"      舊: {old_entry.get('RepoUrl')!r}\n"
                    f"      新: {new_entry.get('RepoUrl')!r}")
        if len(new) < len(old):
            problems.append(f"條目數從 {len(old)} 掉到 {len(new)}")

    if problems:
        print("🔴 repo.json 保險檢查不通過，不要 commit：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("\n  RepoUrl 是 Dalamud 判定「已安裝」的一部分，改了會讓全艦隊的既有安裝\n"
              "  跟 feed 失聯，而且使用者端看不到任何錯誤。\n"
              "  先 `git checkout -- repo.json` 還原，再去查 mirror_releases.py。",
              file=sys.stderr)
        return 1

    print(f"[verify] repo.json OK（{len(new)} 個條目，RepoUrl 全數未變動）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
