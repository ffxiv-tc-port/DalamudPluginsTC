"""用 GitHub App 的私鑰換取 installation token。

為什麼要這個：`release_plugin.py` 原本用使用者自己的 PAT 去 dispatch release.yml，
於是 Actions 的 run 會顯示成該使用者觸發（`actor: Lother`）。GitHub 的 run 一定歸屬到
「使用者」或「GitHub App」——**org 本身不能當 actor**——所以要讓它顯示成組織名義，
唯一的做法是建一個 App 並用它的 installation token 來 dispatch，run 就會顯示
`TCToolBox[bot]`。

沒有裝 PyJWT / requests，所以這裡用 `cryptography` 自己簽 RS256、用 `urllib` 打 API，
不新增任何相依。

設定方式（⚠️ 私鑰走檔案，不要貼進終端機——貼上會被吞掉，曾經因此外洩過真的憑證）：

    setx TCTOOLBOX_APP_ID 123456
    # 私鑰檔放在 repo 之外，例如 D:\ffxiv-tc-port\tctoolbox-app.pem
    setx TCTOOLBOX_APP_KEY D:\ffxiv-tc-port\tctoolbox-app.pem

沒設定就回傳 None，呼叫端會自動退回原本的 PAT 流程（所以這是純加值、不會擋住發版）。
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

ORG = "ffxiv-tc-port"
_API = "https://api.github.com"

# installation token 有效期 1 小時；發版流程遠短於此，但仍留一點餘裕重取。
_TOKEN_TTL_MARGIN_S = 300

_cache: dict[str, tuple[str, float]] = {}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_jwt(app_id: str, key_pem: bytes) -> str:
    """GitHub App 的 JWT：RS256，iss=app id，有效期最長 10 分鐘。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(key_pem, password=None)

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    # iat 往前 60 秒，避開本機與 GitHub 之間的時鐘偏移（偏差會直接被拒絕）
    payload = {"iat": now - 60, "exp": now + 540, "iss": app_id}

    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(signature)}"


def _api(path: str, token: str, method: str = "GET") -> dict:
    req = urllib.request.Request(
        f"{_API}{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ffxiv-tc-port-release",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def get_installation_token(org: str = ORG) -> str | None:
    """回傳可用的 installation token；未設定或失敗時回傳 None（呼叫端退回 PAT）。"""
    app_id = os.environ.get("TCTOOLBOX_APP_ID", "").strip()
    key_path = os.environ.get("TCTOOLBOX_APP_KEY", "").strip()
    if not app_id or not key_path:
        return None

    cached = _cache.get(org)
    if cached and cached[1] - _TOKEN_TTL_MARGIN_S > time.time():
        return cached[0]

    if not os.path.isfile(key_path):
        print(f"[app_token] 找不到私鑰檔 {key_path}，退回使用 PAT")
        return None

    try:
        with open(key_path, "rb") as fh:
            key_pem = fh.read()

        jwt_token = _make_jwt(app_id, key_pem)
        installation = _api(f"/orgs/{org}/installation", jwt_token)
        inst_id = installation["id"]
        created = _api(f"/app/installations/{inst_id}/access_tokens", jwt_token, method="POST")
        token = created["token"]
        # expires_at 例：2026-08-01T01:23:45Z
        expires = time.mktime(time.strptime(created["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
        _cache[org] = (token, expires)
        return token
    except urllib.error.HTTPError as ex:
        body = ex.read().decode(errors="replace")[:200]
        print(f"[app_token] 取得 installation token 失敗（HTTP {ex.code}）：{body}")
    except Exception as ex:  # 任何問題都不該擋住發版
        print(f"[app_token] 取得 installation token 失敗：{ex}")
    return None


if __name__ == "__main__":
    t = get_installation_token()
    if t:
        # 只印前綴，不要把 token 完整印進終端機或 log
        print(f"OK: 取得 installation token（{t[:8]}…，長度 {len(t)}）")
    else:
        print("未設定或取得失敗——發版會退回使用你自己的 PAT。")
