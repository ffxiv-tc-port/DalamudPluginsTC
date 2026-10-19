# -*- coding: utf-8 -*-
"""上傳前的帳號痕跡閘門：mirror 要上傳的插件 zip／source zip 先掃，有真命中就不傳。

契約：要找的字（needle）只從 repo 外的本機檔讀（預設 NEEDLE_FILE，可用環境變數 LEAK_GUARD_NEEDLE_FILE 覆寫），
原始碼與 workflow 都不含它。讀不到 needle 一律不放行（fail-closed），訊息只印長度、不印內容。
真命中＝字詞開頭且大小寫是全小寫／首字大寫／全大寫；字中出現或怪大小寫算誤判、不擋。
"""
import base64
import gzip
import io
import os
import re
import sys
import zipfile

NEEDLE_FILE = os.environ.get("LEAK_GUARD_NEEDLE_FILE", "D:/ffxiv-tc-port-guard/needle.txt")
WORD = re.compile(rb"[A-Za-z0-9_]")
ZIP_MAGIC = b"PK" + bytes([3, 4])
GZ_MAGIC = bytes([0x1F, 0x8B])


def load_needle(path=None):
    try:
        with open(path or NEEDLE_FILE, encoding="utf-8") as f:
            v = f.read().strip().lower()
    except OSError:
        return None
    return v if len(v) >= 4 and re.fullmatch("[a-z0-9_.-]+", v) else None


class Matcher:
    def __init__(self, n):
        self.n = n.encode()
        self.n16 = b"".join(bytes([c, 0]) for c in self.n)
        self.cases = {self.n, self.n.capitalize(), self.n.upper()}
        full = len(self.n) // 3 * 3
        self.b64 = sorted({base64.b64encode(c[:full]) for c in self.cases}) if full >= 3 else []

    def real(self, raw):
        low, cnt = raw.lower(), 0
        for nd, step in ((self.n, 1), (self.n16, 2)):
            s = 0
            while True:
                i = low.find(nd, s)
                if i < 0:
                    break
                prev_word = i - step >= 0 and WORD.match(raw[i - step:i - step + 1])
                if not prev_word and raw[i:i + len(nd)][::step] in self.cases:
                    cnt += 1
                s = i + 1
        for p in self.b64:
            cnt += raw.count(p)
        return cnt

    def real_deep(self, raw, depth=0):
        """自身＋zip（兩層）／gzip 內層的真命中總數，與命中成員名。"""
        total, members = self.real(raw), []
        try:
            if raw[:4] == ZIP_MAGIC and depth < 2:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    for info in z.infolist():
                        if info.is_dir() or info.file_size > 300 * 2 ** 20:
                            continue
                        c, sub = self.real_deep(z.read(info), depth + 1)
                        if c:
                            total += c
                            members.append(info.filename)
                            members.extend(info.filename + "!" + s for s in sub)
            elif raw[:2] == GZ_MAGIC and depth < 2:
                c, _ = self.real_deep(gzip.decompress(raw), depth + 1)
                if c:
                    total += c
                    members.append("<gzip>")
        except Exception as ex:  # 壞掉的壓縮檔不擋上傳，交給原本的流程報錯
            members.append(f"<container-error {type(ex).__name__}>")
        return total, members


def check_files(paths, needle_path=None):
    """回 (ok, 說明)。說明只含檔名、成員名與數字。"""
    n = load_needle(needle_path)
    if n is None:
        return False, f"needle 檔讀不到或格式不對（{needle_path or NEEDLE_FILE}），不放行"
    m = Matcher(n)
    bad = []
    for p in paths:
        with open(p, "rb") as f:
            c, mem = m.real_deep(f.read())
        if c:
            bad.append(f"{os.path.basename(p)} real={c} members={mem[:3]}")
    if bad:
        return False, "擋下：" + "；".join(bad)
    return True, f"乾淨（{len(paths)} 個檔，needle 長度 {len(n)}）"


if __name__ == "__main__":
    ok, why = check_files(sys.argv[1:])
    print(why)
    sys.exit(0 if ok else 1)
