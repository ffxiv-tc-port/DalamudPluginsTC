#!/usr/bin/env python3
"""從 repo.json 的變動產生 feed 的 commit 訊息（繁體中文），印到 stdout。

mirror_releases.py 只改檔案不 commit。以前那一步由呼叫端（人或 agent）自己做，
訊息也是手寫的。改成排程 workflow 之後沒有人在旁邊，所以訊息必須自動產生 ——
而且要看得出「這一筆同步了哪些外掛的哪個版本」，否則 feed 的歷史會退化成
一整排一模一樣的 "Mirror plugin releases"（被刪掉的舊 cron 就是這樣）。

格式沿用既有歷史：

    單一外掛：同步 ICE 7.20.0.32:第二顆星資料拆彈,釣點裸索引改 TryGetValue
    多個外掛：同步 3 個外掛:A 7.20.0.1、B 7.20.0.2、C 7.20.0.3
              （內文一行一個，附各自的 changelog 第一行）

⚠️ 冒號後面接的是該外掛 repo.json 條目裡 Changelog 的第一行 —— 那本來就是
上游 commit 訊息的第一行（見 mirror_releases.get_changelog）。

用法：
    python3 scripts/feed_commit_message.py            # 對 HEAD 比對
    python3 scripts/feed_commit_message.py <ref>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from verify_repo_json import REPO_JSON, by_internal, git_show

SUBJECT_MAX = 120


def changelog_first_line(entry: dict) -> str:
    for line in (entry.get("Changelog") or "").splitlines():
        line = line.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if line:
            return line
    return ""


def collect_changes(old: dict, new: dict) -> list[tuple[str, str, str]]:
    """回傳 [(InternalName, 版本, changelog 第一行), ...]，按名稱排序。

    比對整個條目而不是只比版本號：Changelog / DownloadLink / 描述任何一個變了
    都算這個外掛有更新（版本號沒動但描述變了也該被記下來）。"""
    changed = []
    for name, entry in new.items():
        before = old.get(name)
        if before == entry:
            continue
        version = entry.get("AssemblyVersion") or "?"
        changed.append((name, version, changelog_first_line(entry)))
    changed.sort()
    return changed


def build_message(changed: list[tuple[str, str, str]]) -> str:
    if not changed:
        # repo.json 沒動但 icons/ 或 release-state.json 動了。
        return "同步 feed 附屬資料(圖示或狀態檔)\n"

    if len(changed) == 1:
        name, version, first = changed[0]
        subject = f"同步 {name} {version}" + (f":{first}" if first else "")
        if len(subject) > SUBJECT_MAX:
            subject = subject[:SUBJECT_MAX - 3] + "..."
        return subject + "\n"

    head = "、".join(f"{n} {v}" for n, v, _ in changed)
    subject = f"同步 {len(changed)} 個外掛:{head}"
    if len(subject) > SUBJECT_MAX:
        subject = f"同步 {len(changed)} 個外掛:" + "、".join(n for n, _, _ in changed)
    if len(subject) > SUBJECT_MAX:
        subject = subject[:SUBJECT_MAX - 3] + "..."
    body = "\n".join(f"- {n} {v}:{f}" if f else f"- {n} {v}" for n, v, f in changed)
    return f"{subject}\n\n{body}\n"


def main(argv: list[str]) -> int:
    ref = argv[1] if len(argv) > 1 else "HEAD"

    new = by_internal(REPO_JSON.read_text(encoding="utf-8"))
    old_text = git_show(ref, "repo.json")
    try:
        old = by_internal(old_text) if old_text is not None else {}
    except ValueError:
        old = {}

    message = build_message(collect_changes(old, new))
    # ⚠️ 直接寫 buffer：runner 的 stdout 編碼不見得是 UTF-8（Windows 上是 cp950），
    # 而 git 讀 -F 的訊息檔預設當 UTF-8。
    sys.stdout.buffer.write(message.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
