#!/usr/bin/env python3
"""release_plugin.py `--watch` 組裝邏輯的離線測試。

    python scripts/test_release_plugin_watch.py

不碰網路、不呼叫 `gh`、不推任何 tag、不執行監看管線 —— 每個情境都把
`LOCAL_PATHS` 指到暫存目錄，並用固定值替身換掉四個會出去問人的函式
(`has_uncommitted_changes` / `latest_git_tag` / `tag_points_at_head` /
`release_has_assets`)。`next_tag` 是純函式，照真的算。

為什麼需要這支：`--watch` 那條路的失敗形式**全都是靜默的**。
  ① InternalName 大小寫被「順手修正」→ 監看的是一個不存在的外掛，
     而 SOURCE_REPOS 查無會被印成一行普通訊息，整輪照樣結束。
  ② tag 不是這一輪推的那個(例如退回去問「最新 release」)→ 段 1/段 3 全部
     早就相符，一路綠燈跑完卻**什麼都沒驗到**。
  ③ 有外掛沒觸發成功卻照樣啟動監看 → 監看管線的 exit 0 變成半真的話。
四個情境各釘一條：

  情境 1  全部觸發成功       → 印出的對組表 = 各外掛剛算出來的 next tag
  情境 2  一個沒觸發成功     → 不啟動監看、離開碼 1、列出成功清單
  情境 3  全部無新版可發     → 不納入監看，但 tag 仍要印出來
  情境 4  --watch + --wait   → argparse 直接報錯(離開碼 2)
"""
import io
import pathlib
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import release_plugin as rp  # noqa: E402

TARGETS = ["Artisan", "GatherbuddyReborn", "TCToolbox", "Dynamis"]
# 這一輪各外掛「上一個」tag。故意讓 build number 各不相同，對組錯位才看得出來。
LATEST = {"Artisan": "v7.20.0.80", "GatherbuddyReborn": "v7.20.0.31",
          "TCToolbox": "v7.20.0.48", "Dynamis": "v7.20.0.6"}
EXPECTED_NEXT = {"Artisan": "v7.20.0.81", "GatherbuddyReborn": "v7.20.0.32",
                 "TCToolbox": "v7.20.0.49", "Dynamis": "v7.20.0.7"}

FAILURES = []


def check(label, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def run(argv, tmp, dirty=(), released=False):
    """跑一次 main()，回傳 (stdout, stderr, exit code)。

    🔴 替身一律**從 repo_path 反查外掛名**，不要在呼叫前後暫時改模組層變數：
    release_one() 跑在 ThreadPoolExecutor（預設 8 條）裡，「進來時設好、出去時還原」
    的寫法會被別的執行緒覆蓋掉，實測結果是四個外掛拿到同一個 tag —— 而那正是這支
    測試要抓的錯誤形狀，測試自己踩下去就什麼都測不到了。
    """
    rp.DISPATCHED_TAGS.clear()
    rp.UNCHANGED_TAGS.clear()
    saved = {k: getattr(rp, k) for k in
             ("LOCAL_PATHS", "has_uncommitted_changes", "latest_git_tag",
              "tag_points_at_head", "release_has_assets")}
    # 每個外掛一個自己的目錄，替身才有辦法無狀態地分辨是誰。
    for name in TARGETS:
        (tmp / name).mkdir(exist_ok=True)
    rp.LOCAL_PATHS = {n: str(tmp / n) for n in TARGETS}
    who = lambda repo_path: pathlib.Path(repo_path).name  # noqa: E731
    rp.has_uncommitted_changes = lambda repo_path: who(repo_path) in dirty
    rp.latest_git_tag = lambda repo_path: LATEST[who(repo_path)]
    rp.tag_points_at_head = lambda repo_path, tag: released
    rp.release_has_assets = lambda source_repo, tag: released

    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            sys.argv = ["release_plugin.py", *argv]
            rp.main()
    except SystemExit as exc:
        code = exc.code
    finally:
        for k, v in saved.items():
            setattr(rp, k, v)
    return out.getvalue(), err.getvalue(), code


def watch_pairs(out):
    """從輸出裡撈監看指令列上的 InternalName=tag。"""
    lines = [l for l in out.splitlines() if "release_watch_pipeline.py" in l]
    if len(lines) != 1:
        return None
    return sorted(lines[0].split("release_watch_pipeline.py", 1)[1].split())


# ⚠️ 這幾個情境都帶 --dry-run --watch-dry-run：release_one() 算完 tag 就返回，
# 不會 git tag / git push / workflow run，run_watch() 也只印指令不執行。
BASE = TARGETS + ["--dry-run", "--watch-dry-run"]

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = pathlib.Path(tmpdir)

    print("情境 1  全部觸發成功")
    out, _, code = run(BASE, tmp)
    pairs = watch_pairs(out)
    check("印出恰好一行監看指令", pairs is not None)
    if pairs:
        check("對組表 = 各外掛剛算出來的 next tag",
              pairs == sorted(f"{n}={t}" for n, t in EXPECTED_NEXT.items()), str(pairs))
        # 🔴 大小寫閘門：傳出去的必須是 feed 的 InternalName，不是 GitHub repo 名。
        # GatherbuddyReborn(小寫 b) vs repo GatherBuddyReborn(大寫 B)——改一個字母
        # 等於換一個外掛，既有使用者從此收不到更新，而且完全沒有錯誤訊息。
        check("InternalName 逐字，沒有被「修正」成 repo 名",
              any(p.startswith("GatherbuddyReborn=") for p in pairs)
              and not any(p.startswith("GatherBuddyReborn=") for p in pairs), str(pairs))
    check("只印指令不執行", "只印指令" in out)
    check("離開碼 0", code == 0, str(code))

    print("情境 2  GatherbuddyReborn 沒觸發成功")
    out, _, code = run(BASE, tmp, dirty={"GatherbuddyReborn"})
    check("不啟動監看", "不啟動監看" in out)
    check("沒有印出完整的四個對組", watch_pairs(out) is None or len(watch_pairs(out)) == 3)
    check("列出觸發成功的三個讓人自己決定", "這一輪觸發成功的是" in out)
    check("離開碼 1", code == 1, str(code))

    print("情境 3  全部 HEAD 早就發過且資產齊全")
    out, _, code = run(BASE, tmp, released=True)
    check("明說沒東西可監看", "沒有東西可以監看" in out)
    check("沒監看到的那些連 tag 一起印出來",
          all(f"{n}={t}" in out for n, t in LATEST.items()))
    check("離開碼 0", code == 0, str(code))

    print("情境 4  --watch 與 --wait 併用")
    out, err, code = run(["Artisan", "--wait", "--watch"], tmp)
    check("argparse 報錯", "不能跟 --wait 併用" in err, err.strip()[-80:])
    check("離開碼 2", code == 2, str(code))

print("")
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("all checks passed")
