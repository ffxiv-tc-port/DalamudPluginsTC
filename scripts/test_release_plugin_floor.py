#!/usr/bin/env python3
"""release_plugin.py 版號下限（feed 狀態檔）與「HEAD 上的 tag 沒有 release」分流的離線測試。

    python scripts/test_release_plugin_floor.py

拋棄式 repo＋本機 bare origin；外掛名與 source repo 是假的，推送、dispatch、帳號痕跡閘門、
遠端狀態檔、release 查詢全部換成替身，不碰 GitHub。
"""
import io
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import release_plugin as rp  # noqa: E402

ID = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]
FAILURES = []
DISPATCHED = []
RELEASES = {}   # (source_repo, tag) -> "assets"／"empty"；不在＝沒有 release


def check(label, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


def g(cwd, *args):
    r = subprocess.run(["git", *ID, "-C", str(cwd), *args], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode('utf-8', 'replace')}")
    return r.stdout.decode("utf-8", "replace").strip()


def make_repo(root, name, tags=(), push_tags=True):
    """origin 與本機都在同一顆 HEAD；tags 打在 HEAD（push_tags 時也推上 origin）。"""
    origin, repo = root / f"{name}-origin.git", root / name
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, capture_output=True)
    repo.mkdir()
    g(repo, "init", "-q", "-b", "tc-7.20")
    g(repo, "config", "core.autocrlf", "false")
    g(repo, "remote", "add", "origin", str(origin))
    (repo / "a.txt").write_bytes(b"x\n")
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", "squashed")
    g(repo, "push", "-q", "origin", "tc-7.20")
    for t in tags:
        g(repo, "tag", t)
        if push_tags:
            g(repo, "push", "-q", "origin", t)
    return repo, origin


def remote_tags(origin):
    return sorted(g(origin, "tag").split())


def run(names, paths, remote_state, local_state, argv=(), manifests=([], [])):
    """跑一次 main()；回 (輸出, 例外或 None)。manifests＝(遠端, 本機) repo.json 條目清單。"""
    DISPATCHED.clear()
    rp._feed_state_cache.clear()
    rp._feed_manifest_cache.clear()
    rp.fetch_remote_repo_json = lambda: manifests[0]
    rp.load_local_repo_json = lambda: manifests[1]
    rp.ASSUME_NO_TAGS = False
    rp.DISPATCHED_TAGS.clear()
    rp.UNCHANGED_TAGS.clear()
    rp.LOCAL_PATHS = {n: str(p) for n, p in paths.items()}
    rp.mirror.SOURCE_REPOS = {n: f"example-invalid/{n.replace('Alias', '')}" for n in names}
    rp.fetch_remote_feed_state = lambda: remote_state
    rp.load_local_feed_state = lambda: local_state
    rp.git_push_as_app = lambda repo_path, *args: rp.git(repo_path, *args)
    rp.dispatch_release_run = lambda source_repo, tag, **kw: DISPATCHED.append((source_repo, tag))
    rp.leak_guard_outgoing = lambda *a, **k: None
    rp.release_has_assets = lambda source_repo, tag: RELEASES.get((source_repo, tag)) == "assets"
    rp.release_definitely_missing = lambda source_repo, tag: (source_repo, tag) not in RELEASES
    out, err = io.StringIO(), None
    targets = [n for n in names if not n.endswith("Alias")]
    with redirect_stdout(out):
        sys.argv = ["release_plugin.py", *targets, *argv]
        try:
            rp.main()
        except SystemExit as exc:
            err = exc
    return out.getvalue(), err


def rmtree(p):
    def onexc(func, path, exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    shutil.rmtree(p, onexc=onexc)


def main():
    keys = ("LOCAL_PATHS", "git_push_as_app", "dispatch_release_run", "leak_guard_outgoing", "release_has_assets",
            "release_definitely_missing", "fetch_remote_feed_state", "load_local_feed_state", "ASSUME_NO_TAGS",
            "fetch_remote_repo_json", "load_local_repo_json", "ERA_FLOOR", "era_floor_problem")
    saved = {k: getattr(rp, k) for k in keys}
    saved_sources, saved_argv = rp.mirror.SOURCE_REPOS, sys.argv
    root = pathlib.Path(tempfile.mkdtemp(prefix="release_floor_test_"))
    src = lambda n: f"example-invalid/{n}"
    try:
        print("情境 1  現況：本機 tag 與 feed 一致、release 齊全 ⇒ 跟改前一樣跳過")
        repo, origin = make_repo(root, "Same", tags=["v7.25.0.5"])
        RELEASES[(src("Same"), "v7.25.0.5")] = "assets"
        out, _ = run(["Same"], {"Same": repo}, {"Same": "v7.25.0.5"}, {"Same": "v7.25.0.5"})
        check("跳過、不 dispatch、不提下限", "HEAD already released as v7.25.0.5" in out and not DISPATCHED and "版號下限" not in out, out)

        print("情境 2  現況：HEAD 比 tag 新 ⇒ 本機 tag＋1（下限不介入）")
        repo, origin = make_repo(root, "Ahead", tags=["v7.25.0.5"])
        (repo / "b.txt").write_bytes(b"y\n")
        g(repo, "add", "-A")
        g(repo, "commit", "-q", "-m", "work")
        out, _ = run(["Ahead"], {"Ahead": repo}, {"Ahead": "v7.25.0.5"}, {"Ahead": "v7.25.0.5"})
        check("推 v7.25.0.6 並 dispatch", remote_tags(origin) == ["v7.25.0.5", "v7.25.0.6"] and DISPATCHED == [(src("Ahead"), "v7.25.0.6")], out)

        print("情境 3  tag 全刪（本機與遠端都沒有）⇒ feed 現行版本＋1，不是 .0.1")
        repo, origin = make_repo(root, "NoTags")
        out, _ = run(["NoTags"], {"NoTags": repo}, {"NoTags": "v7.25.0.7"}, {"NoTags": "v7.25.0.7"})
        check("推 v7.25.0.8", remote_tags(origin) == ["v7.25.0.8"] and DISPATCHED == [(src("NoTags"), "v7.25.0.8")], out)
        check("輸出說明下限來自 feed 狀態檔", "版號下限取自 feed 狀態檔：v7.25.0.7" in out, out)
        repo, origin = make_repo(root, "NoTagsNoState")
        out, _ = run(["NoTagsNoState"], {"NoTagsNoState": repo}, {}, {})
        check("負對照：狀態檔沒有這個插件 ⇒ 退回 v7.25.0.1（證明救回版號的是下限）", remote_tags(origin) == ["v7.25.0.1"], out)

        print("情境 4  本機狀態檔落後遠端一輪 ⇒ 以遠端為準")
        repo, origin = make_repo(root, "StaleLocal")
        out, _ = run(["StaleLocal"], {"StaleLocal": repo}, {"StaleLocal": "v7.25.0.61"}, {"StaleLocal": "v7.25.0.60"})
        check("推 v7.25.0.62", remote_tags(origin) == ["v7.25.0.62"], out)

        print("情境 5  遠端狀態檔讀不到")
        repo, origin = make_repo(root, "NoRemoteState")
        out, _ = run(["NoRemoteState"], {"NoRemoteState": repo}, None, {"NoRemoteState": "v7.25.0.60"})
        check("本機沒有 tag ⇒ 大聲失敗、什麼都不推", "[FAIL] NoRemoteState" in out and "讀不到" in out and remote_tags(origin) == [] and not DISPATCHED, out)
        repo, origin = make_repo(root, "NoRemoteButTags", tags=["v7.25.0.60"])
        (repo / "b.txt").write_bytes(b"y\n")
        g(repo, "add", "-A")
        g(repo, "commit", "-q", "-m", "work")
        out, _ = run(["NoRemoteButTags"], {"NoRemoteButTags": repo}, None, {"NoRemoteButTags": "v7.25.0.60"})
        check("本機 tag 不低於本機狀態 ⇒ 照常 +1", remote_tags(origin) == ["v7.25.0.60", "v7.25.0.61"], out)

        print("情境 6  同一個來源 repo 的別名條目版號較高 ⇒ 取較高者")
        repo, origin = make_repo(root, "Host")
        out, _ = run(["Host", "HostAlias"], {"Host": repo}, {"Host": "v7.25.0.3", "HostAlias": "v7.25.0.4"}, {})
        check("推 v7.25.0.5", remote_tags(origin) == ["v7.25.0.5"], out)

        print("情境 7  tag 在 HEAD、來源 repo 沒有 release")
        repo, origin = make_repo(root, "Shipped", tags=["v7.25.0.9"])
        out, _ = run(["Shipped"], {"Shipped": repo}, {"Shipped": "v7.25.0.9"}, {"Shipped": "v7.25.0.9"})
        check("feed 已發過這一版 ⇒ 不重 dispatch 同版號，改打 v7.25.0.10",
              DISPATCHED == [(src("Shipped"), "v7.25.0.10")] and "改打下一版" in out, out)
        repo, origin = make_repo(root, "CiFailed", tags=["v7.25.0.9"])
        out, _ = run(["CiFailed"], {"CiFailed": repo}, {"CiFailed": "v7.25.0.8"}, {"CiFailed": "v7.25.0.8"})
        check("feed 還停在上一版（CI 失敗過）⇒ 跟改前一樣重 dispatch 同一顆",
              DISPATCHED == [(src("CiFailed"), "v7.25.0.9")] and remote_tags(origin) == ["v7.25.0.9"], out)
        repo, origin = make_repo(root, "EmptyRel", tags=["v7.25.0.9"])
        RELEASES[(src("EmptyRel"), "v7.25.0.9")] = "empty"
        out, _ = run(["EmptyRel"], {"EmptyRel": repo}, {"EmptyRel": "v7.25.0.9"}, {"EmptyRel": "v7.25.0.9"})
        check("release 在但沒有資產 ⇒ 重 dispatch 同一顆（不多打版號）", DISPATCHED == [(src("EmptyRel"), "v7.25.0.9")], out)

        print("情境 8  本機殘留舊 tag、遠端 tag 已刪")
        repo, origin = make_repo(root, "LocalOnly", tags=["v7.25.0.5"], push_tags=False)
        out, _ = run(["LocalOnly"], {"LocalOnly": repo}, {"LocalOnly": "v7.25.0.5"}, {"LocalOnly": "v7.25.0.5"})
        check("推 v7.25.0.6", remote_tags(origin) == ["v7.25.0.6"] and DISPATCHED == [(src("LocalOnly"), "v7.25.0.6")], out)

        print("情境 9  --assume-no-tags")
        repo, origin = make_repo(root, "Rehearse", tags=["v7.25.0.5"])
        RELEASES[(src("Rehearse"), "v7.25.0.5")] = "assets"
        out, _ = run(["Rehearse"], {"Rehearse": repo}, {"Rehearse": "v7.25.0.5"}, {}, argv=["--dry-run", "--assume-no-tags"])
        check("dry-run 印出 next tag: v7.25.0.6、沒有推也沒有 dispatch",
              "[Rehearse] next tag: v7.25.0.6" in out and remote_tags(origin) == ["v7.25.0.5"] and not DISPATCHED, out)
        _, err = run(["Rehearse"], {"Rehearse": repo}, {"Rehearse": "v7.25.0.5"}, {}, argv=["--assume-no-tags"])
        check("不帶 --dry-run ⇒ 直接報錯結束", err is not None and err.code == 2 and remote_tags(origin) == ["v7.25.0.5"])

        print("情境 10  --tag 低於 feed 下限")
        repo, origin = make_repo(root, "Forced")
        out, _ = run(["Forced"], {"Forced": repo}, {"Forced": "v7.25.0.7"}, {}, argv=["--tag", "v7.25.0.7"])
        check("跳過並印出原因、什麼都不推", "不高於既有最新 tag v7.25.0.7" in out and remote_tags(origin) == [], out)

        def entry(name, ver):
            return {"InternalName": name, "AssemblyVersion": ver}

        def pushed(label, origin, tag, out, err, name, before=()):
            check(label, remote_tags(origin) == sorted([*before, tag]) and DISPATCHED == [(src(name), tag)] and err is None, out)

        def refused(label, origin, out, err, needle, before=()):
            check(label, remote_tags(origin) == sorted(before) and not DISPATCHED and needle in out
                  and err is not None and err.code == 3, out + f" exit={getattr(err, 'code', None)}")

        print("情境 11  紀元寫成 (7, 3)（應為 (7, 30)），feed 在 7.25")
        rp.ERA_FLOOR = (7, 3)
        repo, origin = make_repo(root, "EraTypo", tags=["v7.25.0.5"])
        (repo / "b.txt").write_bytes(b"y\n")
        g(repo, "add", "-A")
        g(repo, "commit", "-q", "-m", "work")
        out, err = run(["EraTypo"], {"EraTypo": repo}, {"EraTypo": "v7.25.0.5"}, {})
        check("啟動就拒絕、指出要寫 (7, 30)、什麼都不推", err is not None and "(7, 30)" in str(err.code)
              and remote_tags(origin) == ["v7.25.0.5"] and not DISPATCHED, f"{out} exit={getattr(err, 'code', None)}")
        rp.era_floor_problem = lambda floor=None: None
        out, err = run(["EraTypo"], {"EraTypo": repo}, {"EraTypo": "v7.25.0.5"}, {})
        refused("拿掉啟動檢查 ⇒ 逐插件的紀元防呆仍擋下（改前會靜默推 v7.25.0.6）", origin, out, err, "不在 ERA_FLOOR", ["v7.25.0.5"])
        out, err = run(["EraTypo"], {"EraTypo": repo}, {"EraTypo": "v7.25.0.5"}, {}, argv=["--tag", "v7.3.0.1"])
        refused("--tag v7.3.0.1 ⇒ 擋下、exit 3", origin, out, err, "v7.3.0.1", ["v7.25.0.5"])
        rp.era_floor_problem = saved["era_floor_problem"]
        rp.ERA_FLOOR = (7, 20)
        out, err = run(["EraTypo"], {"EraTypo": repo}, {"EraTypo": "v7.25.0.5"}, {})
        refused("ERA_FLOOR 過期成 (7, 20) ⇒ 同樣擋下", origin, out, err, "不在 ERA_FLOOR", ["v7.25.0.5"])
        rp.ERA_FLOOR = saved["ERA_FLOOR"]
        out, err = run(["EraTypo"], {"EraTypo": repo}, {"EraTypo": "v7.25.0.5"}, {})
        pushed("負對照：ERA_FLOOR (7, 25) ⇒ 同一個 repo 照常推 v7.25.0.6", origin, "v7.25.0.6", out, err, "EraTypo", ["v7.25.0.5"])

        print("情境 12  新紀元 (7, 30)，feed 在 7.25")
        rp.ERA_FLOOR = (7, 30)
        repo, origin = make_repo(root, "EraNew", tags=["v7.25.0.5"])
        (repo / "b.txt").write_bytes(b"y\n")
        g(repo, "add", "-A")
        g(repo, "commit", "-q", "-m", "work")
        out, err = run(["EraNew"], {"EraNew": repo}, {"EraNew": "v7.25.0.5"}, {},
                       manifests=([entry("EraNew", "7.25.0.5")], []))
        pushed("推 v7.30.0.1", origin, "v7.30.0.1", out, err, "EraNew", ["v7.25.0.5"])
        repo, origin = make_repo(root, "EraBrandNew")
        out, err = run(["EraBrandNew"], {"EraBrandNew": repo}, {}, {})
        pushed("feed 沒有這個插件（新插件）⇒ 推 v7.30.0.1", origin, "v7.30.0.1", out, err, "EraBrandNew")
        rp.ERA_FLOOR = saved["ERA_FLOOR"]

        print("情境 13  同紀元遞增，repo.json 與狀態檔一致")
        repo, origin = make_repo(root, "SameEra")
        out, err = run(["SameEra"], {"SameEra": repo}, {"SameEra": "v7.25.0.40"}, {},
                       manifests=([entry("SameEra", "7.25.0.40")], [entry("SameEra", "7.25.0.40")]))
        pushed("推 v7.25.0.41", origin, "v7.25.0.41", out, err, "SameEra")

        print("情境 14  --tag 高於狀態檔、但不高於 repo.json 的 AssemblyVersion")
        repo, origin = make_repo(root, "ManBehind")
        out, err = run(["ManBehind"], {"ManBehind": repo}, {"ManBehind": "v7.25.0.5"}, {},
                       argv=["--tag", "v7.25.0.8"], manifests=([entry("ManBehind", "7.25.0.9")], []))
        refused("擋下並指出 repo.json、exit 3", origin, out, err, "repo.json（遠端）ManBehind AssemblyVersion=7.25.0.9")
        out, err = run(["ManBehind"], {"ManBehind": repo}, {"ManBehind": "v7.25.0.5"}, {},
                       argv=["--tag", "v7.25.0.8"], manifests=([], [entry("Other", "7.25.0.99")]))
        pushed("負對照：repo.json 沒有它 ⇒ 推 v7.25.0.8", origin, "v7.25.0.8", out, err, "ManBehind")
        repo, origin = make_repo(root, "AutoBehind")
        out, err = run(["AutoBehind"], {"AutoBehind": repo}, {"AutoBehind": "v7.25.0.5"}, {},
                       manifests=([], [entry("AutoBehind", "7.25.0.6")]))
        refused("自動 +1 撞上本機 repo.json 已有的版本 ⇒ 擋下", origin, out, err, "repo.json（本機）")

        print("情境 15  exit 碼")
        repo, origin = make_repo(root, "Forced2")
        out, err = run(["Forced2"], {"Forced2": repo}, {"Forced2": "v7.25.0.7"}, {}, argv=["--tag", "v7.25.0.6"])
        refused("--tag 低於 feed ⇒ exit 3（改前 exit 0）", origin, out, err, "不高於既有最新 tag v7.25.0.7")
    finally:
        for k, v in saved.items():
            setattr(rp, k, v)
        rp.mirror.SOURCE_REPOS = saved_sources
        sys.argv = saved_argv
        rp._feed_state_cache.clear()
        rmtree(root)

    print("")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
