# feed mirror workflow 背景筆記

本檔收「同步外掛發版到 feed」這個 workflow（現檔名 `.github/workflows/feed-mirror.yml`）
的沿革與事故紀錄。workflow 檔本身只留契約型註解（跑在哪、為什麼這樣寫），
帶日期、run id、事故敘事的內容一律搬到這裡。

## 為什麼是排程 workflow，不是 repository_dispatch

- 本機 `scripts/release_plugin.py` 只負責「推 tag + 觸發外掛 repo 的 `release.yml`」
  就返回，完全不等 CI（以前要等 60~190 秒/外掛，一輪十幾個版本＝半小時的空窗）。
- 誰把建好的 release 同步進 `repo.json`？就是這個 workflow：
  1. 本機 `release_plugin.py` → tag + push + dispatch → 結束（秒級）
  2. 外掛 repo 的 `release.yml` → 建置 + 建 release
  3. 這個 workflow（排程）→ `mirror_releases.py` → 有 diff 就 commit + push
- 為什麼不用跨 repo 的 `repository_dispatch`：那需要在艦隊所有外掛 repo 裡各放一顆
  PAT 或 App token 當 secret，而「不要再建新的 PAT」是紅線。排程只用預設的
  `GITHUB_TOKEN`（後來改用 App token，見下），零新增秘密。

## 沿革

- 這個 workflow 是 2026-07-17 被刪掉的 `mirror-releases.yml` 的復活版。當初刪掉的理由是
  「每小時跑一次，沒變更也燒一個 Actions run」——那個理由在 2026-08-01 org 全公開
  之後已經失效（公開 repo 的 Actions 免費）。舊版有三個缺陷，復活時都修掉了：
  1. 用 `secrets.MIRROR_TOKEN`（一顆 PAT）。改成全 public 後預設 `GITHUB_TOKEN` 就讀得到。
  2. commit 身分是 `github-actions[bot]`，訊息永遠是 `"Mirror plugin releases"`。改成
     `Claude Sonnet 5 <noreply@anthropic.com>`，訊息由 `scripts/feed_commit_message.py` 產生。
  3. 沒有任何失敗偵測。`mirror_releases.py` 是逐外掛 catch 例外的：某個外掛炸了
     只印一行 `[FAIL]` 然後照樣以 0 離開，run 是綠的。舊版會把這種失敗完全吃掉。
- 2026-08-18 再改一次（`[warn]` 分級）。上一版的偵測是「log 裡出現任何 `[FAIL]` 就
  exit 1」，於是 2026-08-17 晚 GitHub 平台事故時，單一外掛的暫時性 API 錯誤讓整輪
  FAIL，其餘 50 幾個外掛已經建好的 release 全部沒能寫進 feed——一個外掛的暫時性
  錯誤擋住所有人的更新。現在分級由 `mirror_releases.py` 自己做（規則在該腳本
  `MirrorReport` 上方註解），workflow 只看它的離開碼：
  - `0` = 成功，可能帶 `[warn]`（那些外掛沿用 feed 既有條目，下一班自動重試）。
  - `2` = 硬失敗：①全部外掛都失敗（平台級）②feed 檔案寫入失敗③失敗的外掛在
    `repo.json` 沒有既有條目可沿用（新外掛首發不能靜默漏）。
  - 「成功但有 warn」與「全綠」要一眼分得出來：腳本把警告計數表寫在 run summary
    最上面，並對每筆 warn 送一則 annotation。

## 2026-09-17：轉 private 的兩項改動

準備全 org 轉 private，這裡跟著改兩件事（見 `docs/private-migration-design.md` 四-11）：

1. `runs-on` 改 self-hosted。理由不是省 Actions 分鐘數（「公開 repo 的 Actions
   免費」這句話只在這個 repo 公開時成立；一旦這個 repo 也轉 private，
   GitHub-hosted 就要吃 org 的 Free 額度，而這個排程是 */15＝2,880 run/月，
   會直接把 2,000 分鐘/月的額度打爆）。
2. `mirror_releases.py` 打的每一個 `gh api repos/<外掛>/...` 都是別的 repo——
   「現在全 public，預設 `GITHUB_TOKEN` 就讀得到」這句話同樣只在來源外掛 repo 是
   public 時成立。org 全 private 之後，這個 workflow 自己的
   `permissions.contents: write` 只授權這個 repo，讀不到別的 org repo 的
   release——於是改用 App token（`scripts/app_token.py` 已有的機制；本機發版
   也是靠它讓 run 的 actor 顯示成 `tctoolbox[bot]` 而不是個人帳號）。env 名沿用
   `app_token.py` 已經在讀的那兩個：`TCTOOLBOX_APP_ID`（App 的數字 ID）／
   `TCTOOLBOX_APP_KEY`。這兩個 secret 2026-09-17 已由使用者在 org 層建好，但
   `TCTOOLBOX_APP_KEY` 存的是 PEM 內容本身（不是路徑）；`app_token.py` 的
   `_load_key_pem()` 已改成兩種形狀都吃（用開頭字串 `-----BEGIN` 分辨），本機
   路徑用法沒受影響——兩向都已跑過 `--selftest` 校準＋對真的 App 實測過。

### 兩條硬相依（缺一都會讓 workflow 靜默停擺）

必須跟「把 DalamudPluginsTC 切成 private」同一批生效，不能提前推：

- (a) 2026-09-17 實查 org 唯一的 self-hosted runner（`DESKTOP-U46D9DM`，labels
  `self-hosted`/`Windows`/`X64`）掛在 `Default` runner group，該 group
  `allows_public_repositories=false`——只要 DalamudPluginsTC 還是 public，
  `runs-on: [self-hosted, Windows, X64]` 的 job 會排隊排到天荒地老（不會報錯，
  也不會顯示失敗，run 就是一直 Queued）。
- (b) 即使 (a) 解決了、job 真的排到了 runner：`gh api
  orgs/ffxiv-tc-port/actions/secrets/TCTOOLBOX_APP_KEY` 實查
  `visibility: "private"`——這是 GitHub org secret 的能見度旗標，意思是「只曝光給
  private repo」。DalamudPluginsTC 還是 public 的那段時間，
  `${{ secrets.TCTOOLBOX_APP_KEY }}` 在這個 repo 的 workflow 裡會解析成空字串，
  `app_token.py` 印「未設定」退回本機 `gh auth` 身分（self-hosted runner 上沒登入
  過，等於直接斷頭）。

兩者都是靜默的（不報錯、不顯示失敗、run 頁面看起來正常或乾脆排隊中），feed 永遠
不會再更新，不會有任何一則錯誤訊息主動找上人。

## 其他實作細節的由來

- 刻意不用 `actions/setup-python`：它的安裝腳本要改登錄檔，自架 runner 的
  NETWORK SERVICE 沒有那個權限（實測 run `35215555381`）。改吃機器層 Python，
  用絕對路徑（服務帳號的環境區塊不一定跟著機器 PATH 更新，實測 run
  `35218030015` 仍 `command not found`）。
- `concurrency.cancel-in-progress: false`：取消會整個丟掉一次同步，下一班要等
  15 分鐘才來，而「少同步一次」是靜默的（feed 看起來正常，只是少了一個版本）。

## 2026-09-17：workflow 實體損壞事件

**症狀**：dispatch 之後 check-suite 永遠 `queued`、`runs=0`，無法取消；連 19:57
跑成功過的舊 commit 也一樣（開分支重 dispatch 同一顆 workflow 檔仍卡住）。時間點
對應批次刪除了這個 workflow 的 600 個歷史 run。

**排除過的原因**：
- concurrency group 換名（原本已從 `mirror-feed` 換成 `mirror-feed-2`，理由是舊
  群組被一個幽靈 run 佔住、新 run 全部停在「尚未進入佇列」）——換群組名沒解決。
- self-hosted runner 本身：同時段 Artisan（另一個外掛 repo）在同一台 runner 上
  正常跑完，排除 runner 掛掉。
- org Actions 政策：查過是 `all`（允許所有 workflow），不是政策擋下。
- 使用者手動在 repo 設定裡關閉又重新開啟 Actions：無效，症狀依舊。

**修法**：GitHub 對 workflow 的身分鍵是**檔案路徑**，不是 `name:` 欄位或內容。
把檔案從 `mirror-releases.yml` 改名成 `feed-mirror.yml`＝建立一個全新的 workflow
實體（新的 workflow id），繞開壞掉的舊實體。邏輯本身（schedule、concurrency 語意、
`self-hosted` runner、App token、失敗分級、commit+push）逐字等價，只有
`concurrency.group` 跟著改名成 `feed-mirror`（配合新檔名，避免沿用可能同樣卡住的
舊群組名）。

## 2026-10-27：轉公開，改代管 runner（B6）

公開 repo 的 self-hosted job 被 runner group（`allows_public_repositories=false`）擋住、永遠排隊，
而排程排隊不會報錯——不改 workflow 就切公開＝feed 當下靜默停更。逐項會壞的地方與修法：

| 會壞的地方 | 公開＋代管後的樣子 | 修法 |
|---|---|---|
| `runs-on: [self-hosted, Windows, X64]` | 永遠 queued | `windows-latest`（腳本只在 Windows 上驗過：檔名大小寫不敏感、symlink 落成純文字檔） |
| `C:/Program Files/Python313/python.exe` 絕對路徑、`pip --user` | 代管機沒有這個路徑 | `actions/setup-python` 3.13＋`python` |
| leak guard 讀 runner 本機 needle 檔 | 讀不到＝每個外掛的上傳被擋成 `[warn]`，run 綠、feed 不收新版 | repo secret `LEAK_GUARD_NEEDLE` 在獨立一步寫進 `RUNNER_TEMP` 檔（`LEAK_GUARD_NEEDLE_FILE`）並先跑一次 `leak_guard.py` 驗格式；讀不到整輪紅 |
| leak guard 成功訊息印 needle 長度 | 公開 run log 人人可讀 | 不印長度 |
| 行尾 | 代管映像的 `core.autocrlf` 不保證；沒正規化＝每輪整份 repo.json 換成 CRLF | checkout 前 `git config --global core.autocrlf true`（與 self-hosted 相同的正規化） |
| token | — | **維持 App token**：GatherBuddyReborn 仍 private（GITHUB_TOKEN 讀不到）；一輪全掃約 220 次 API×每小時 4 輪，逼近 GITHUB_TOKEN 每 repo 每小時 1000 次上限；mirror release 的作者維持 `tctoolbox[bot]`。寫回 main 本來就是 checkout 帶的 GITHUB_TOKEN（`permissions: contents: write`），不變 |
| 長路徑 | 代管機 `RUNNER_TEMP`＝`D:\a\_temp`，比 self-hosted 服務帳號短 | 不用改（clone 仍帶 `core.longpaths=true`） |
| 排程 | 公開 repo 60 天沒有活動會被停用 | feed commit 頻繁，正常不會觸發；長期沒有發版時要留意 |
