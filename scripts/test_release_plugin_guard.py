#!/usr/bin/env python3
"""release_plugin.py 推 tag 前帳號痕跡閘門的離線測試。

    python scripts/test_release_plugin_guard.py

拋棄式 repo＋本機 bare origin；外掛名與 source repo 都是假的，推送與 dispatch 換成替身，
不碰 GitHub。needle 用合成字（環境變數 ACCT_PURGE_NEEDLE），真的 guard.py 照跑。
找不到 guard.py 時 exit 2（不是通過）。
"""
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import release_plugin as rp  # noqa: E402

NEEDLE = "zqxvtest"
DIRTY = ("see https://github.com/" + NEEDLE.capitalize() + "/Repo\n").encode()
CLEAN = ("var al" + NEEDLE[0] + NEEDLE[1].upper() + NEEDLE[2:] + "s = 1;\n").encode()
BASE_TAG, NEXT_TAG = "v7.25.99.1", "v7.25.99.2"
ID = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]

FAILURES = []
DISPATCHED = []


def check(label, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def g(cwd, *args):
    r = subprocess.run(["git", *ID, "-C", str(cwd), *args], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode('utf-8', 'replace')}")
    return r.stdout.decode("utf-8", "replace").strip()


def commit(repo, files, msg):
    for name, data in files.items():
        (repo / name).write_bytes(data)
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return g(repo, "rev-parse", "HEAD")


def make_repo(root, name, push_head=False, head_files=None, head_msg="work"):
    """origin 有 c1＋BASE_TAG；本機 HEAD＝c2（push_head 時也推上 origin）。"""
    origin, repo = root / f"{name}-origin.git", root / name
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, capture_output=True)
    repo.mkdir()
    g(repo, "init", "-q", "-b", "tc-7.20")
    g(repo, "config", "core.autocrlf", "false")
    g(repo, "remote", "add", "origin", str(origin))
    commit(repo, {"a.txt": b"base\n"}, "init")
    g(repo, "tag", BASE_TAG)
    g(repo, "push", "-q", "origin", "tc-7.20", BASE_TAG)
    commit(repo, head_files or {"b.txt": CLEAN}, head_msg)
    if push_head:
        g(repo, "push", "-q", "origin", "tc-7.20")
    return repo, origin


def remote_has(origin, tag):
    return bool(g(origin, "tag", "--list", tag))


def local_has(repo, tag):
    return bool(g(repo, "tag", "--list", tag))


def install_stubs(names):
    """外掛名與 source repo 一律是假的；替身之外的任何路徑都碰不到真的 repo。"""
    rp.mirror.SOURCE_REPOS = {n: f"example-invalid/{n}" for n in names}
    rp.git_push_as_app = lambda repo_path, *args: rp.git(repo_path, *args)
    rp.dispatch_release_run = lambda source_repo, tag, **kw: DISPATCHED.append((source_repo, tag))
    rp.release_has_assets = lambda source_repo, tag: False
    rp.feed_state = lambda: ({}, {})
    rp.feed_manifests = lambda: ([], [])


def run_main(names, paths):
    import io
    from contextlib import redirect_stdout
    DISPATCHED.clear()
    rp.LOCAL_PATHS = {n: str(p) for n, p in paths.items()}
    install_stubs(names)
    out = io.StringIO()
    with redirect_stdout(out):
        sys.argv = ["release_plugin.py", *names]
        rp.main()
    return out.getvalue()


def release_one_raises(name, repo):
    rp.LOCAL_PATHS = {name: str(repo)}
    install_stubs([name])
    try:
        rp.release_one(name, f"example-invalid/{name}")
    except RuntimeError as exc:
        return str(exc)
    return None


def rmtree(p):
    def onexc(func, path, exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    shutil.rmtree(p, onexc=onexc)


def main():
    if rp._leak_guard_path() is None:
        print("找不到 guard.py，這支測試無法驗證閘門（exit 2，不是通過）")
        return 2
    saved = {k: getattr(rp, k) for k in ("LOCAL_PATHS", "git_push_as_app", "dispatch_release_run",
                                         "release_has_assets", "feed_state", "feed_manifests")}
    saved_sources = rp.mirror.SOURCE_REPOS
    saved_env = {k: os.environ.get(k) for k in ("ACCT_PURGE_NEEDLE", "FLEET_LEAK_GUARD")}
    os.environ["ACCT_PURGE_NEEDLE"] = NEEDLE
    os.environ.pop("FLEET_LEAK_GUARD", None)
    root = pathlib.Path(tempfile.mkdtemp(prefix="release_guard_test_"))
    try:
        print("情境 1  一個乾淨、一個內容有命中：乾淨的照發，有命中的擋下")
        clean, clean_o = make_repo(root, "GuardClean", head_files={"b.txt": CLEAN})
        dirty, dirty_o = make_repo(root, "GuardDirty", head_files={"b.txt": DIRTY})
        out = run_main(["GuardClean", "GuardDirty"], {"GuardClean": clean, "GuardDirty": dirty})
        check("乾淨的 tag 推上 origin", remote_has(clean_o, NEXT_TAG))
        check("乾淨的有 dispatch", ("example-invalid/GuardClean", NEXT_TAG) in DISPATCHED, str(DISPATCHED))
        check("有命中的 tag 沒推上 origin", not remote_has(dirty_o, NEXT_TAG))
        check("有命中的本機 tag 已收回", not local_has(dirty, NEXT_TAG))
        check("有命中的沒有 dispatch", all(s != "example-invalid/GuardDirty" for s, _ in DISPATCHED))
        check("大聲報錯：[FAIL] 與 BLOCK 都印出",
              "[FAIL] GuardDirty" in out and "BLOCK" in out and "擋下" in out)
        check("總結 1 成功 1 失敗", "1 succeeded, 1 failed/skipped: ['GuardDirty']" in out)
        check("輸出不含 needle 原文", NEEDLE not in out.lower())

        print("情境 2  只有 commit 訊息有命中")
        repo, origin = make_repo(root, "MsgDirty", head_msg="fix path " + NEEDLE.capitalize() + "/x")
        err = release_one_raises("MsgDirty", repo)
        check("擋下", err is not None and "擋下" in err, str(err))
        check("沒推、本機 tag 已收回", not remote_has(origin, NEXT_TAG) and not local_has(repo, NEXT_TAG))

        print("情境 3  有命中的 commit 早已在 origin：這次推 tag 不會多出東西，放行")
        repo, origin = make_repo(root, "AlreadyRemote", push_head=True, head_files={"b.txt": DIRTY})
        err = release_one_raises("AlreadyRemote", repo)
        check("放行並推上 tag", err is None and remote_has(origin, NEXT_TAG), str(err))

        print("情境 4  annotated tag 物件本身有命中")
        repo, origin = make_repo(root, "TagObj", push_head=True)
        g(repo, "tag", "-a", "vtagobj", "-m", "by " + NEEDLE.capitalize())
        try:
            rp.leak_guard_outgoing(repo, "refs/tags/vtagobj", "TagObj")
            check("tag 物件被掃到並擋下", False, "沒有擲例外")
        except RuntimeError as exc:
            check("tag 物件被掃到並擋下", "擋下" in str(exc), str(exc))

        print("情境 5  對照：換一個 needle，情境 1 的髒 repo 就放行（證明擋的原因是 needle）")
        os.environ["ACCT_PURGE_NEEDLE"] = NEEDLE + "zz"
        repo, origin = make_repo(root, "OtherNeedle", head_files={"b.txt": DIRTY})
        err = release_one_raises("OtherNeedle", repo)
        check("放行", err is None and remote_has(origin, NEXT_TAG), str(err))
        os.environ["ACCT_PURGE_NEEDLE"] = NEEDLE

        print("情境 6  fail-closed：找不到 guard、origin 連不上")
        os.environ["FLEET_LEAK_GUARD"] = str(root / "no-such-guard.py")
        repo, origin = make_repo(root, "NoGuard")
        err = release_one_raises("NoGuard", repo)
        check("找不到 guard 不放行", err is not None and "找不到" in err, str(err))
        check("沒推、本機 tag 已收回", not remote_has(origin, NEXT_TAG) and not local_has(repo, NEXT_TAG))
        os.environ.pop("FLEET_LEAK_GUARD", None)
        repo, origin = make_repo(root, "NoRemote")
        g(repo, "remote", "set-url", "origin", str(root / "missing-origin.git"))
        err = release_one_raises("NoRemote", repo)
        check("ls-remote 失敗不放行", err is not None, str(err))
        check("本機 tag 已收回", not local_has(repo, NEXT_TAG))
    finally:
        for k, v in saved.items():
            setattr(rp, k, v)
        rp.mirror.SOURCE_REPOS = saved_sources
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        rmtree(root)

    print("")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
