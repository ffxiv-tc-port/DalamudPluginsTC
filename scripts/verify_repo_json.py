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
1. 工作樹的 repo.json 仍是合法 JSON 陣列，每個條目都有 InternalName。
2. 舊有的每個 InternalName 都還在（mirror 不該讓條目消失）。
3. 舊有條目的 RepoUrl 一字不差。
4. 每個條目都有非空的 RepoUrl（新加的條目也要有）。
5. 條目總數沒有減少。

新增條目是允許的（新外掛上架）。

用法：
    python3 scripts/verify_repo_json.py            # 對 HEAD 比對
    python3 scripts/verify_repo_json.py <ref>      # 對指定 ref 比對

離開碼 0 = 沒問題；1 = 有問題（訊息印在 stderr）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_JSON = REPO_ROOT / "repo.json"

# ⚠️ 這支腳本的訊息全是中文，而它幾乎總是被當子行程呼叫（stdout 是 pipe）。
# Windows 上 Python 對 pipe 用 **locale** 編碼（cp950），呼叫端卻用 utf-8 解碼 ——
# 中文於是變成一串 U+FFFD，接著呼叫端要把 U+FFFD 印到 cp950 主控台時直接
# UnicodeEncodeError 中止。2026-08-03 實際害 --wait 發版停在 mirror 之後、
# commit 之前。這裡固定成 UTF-8，讓輸出跟呼叫端的解碼假設一致。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def git_show(ref: str, path: str) -> str | None:
    """讀出某個 ref 上的檔案內容；讀不到回 None（例如 repo.json 是全新檔案）。

    ⚠️ 顯式 encoding="utf-8"：repo.json 裡有大量中文，text=True 會用 runner 的
    locale 解碼（Windows 上是 cp950），直接 UnicodeDecodeError。
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{ref}:{path}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return None
    return result.stdout


def by_internal(text: str) -> dict:
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
        out[name] = entry
    return out


def main(argv: list[str]) -> int:
    ref = argv[1] if len(argv) > 1 else "HEAD"

    try:
        new = by_internal(REPO_JSON.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"🔴 工作樹的 repo.json 讀不出來或格式不對：{exc}", file=sys.stderr)
        return 1

    problems: list[str] = []

    for name, entry in sorted(new.items()):
        if not (entry.get("RepoUrl") or "").strip():
            problems.append(f"{name}: RepoUrl 是空的")

    old_text = git_show(ref, "repo.json")
    if old_text is None:
        print(f"[verify] {ref}:repo.json 讀不到（首次建立？），只檢查目前內容")
    else:
        try:
            old = by_internal(old_text)
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
