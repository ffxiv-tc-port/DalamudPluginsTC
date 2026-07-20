# Changelog

僅記錄面向玩家的重大更新，例如新收錄插件、繁體中文化進度、重大問題修復。完整版本記錄請見各插件自己的 GitHub Releases。

## 2026-07-20

### 中文化

大規模擴充繁體中文化覆蓋率，本次涵蓋以下插件的介面翻譯（含先前僅有插件說明翻譯、介面完全未翻譯的項目）：

- **新完成介面中文化**：ItemVendorLocation、HuntHelper、SonarPlugin、WrathCombo、Lifestream、PalacePal、YesAlready（補完遺漏分頁）、QoLBar（補完主設定與深層編輯器）、SomethingNeedDoing（巨集編輯器、設定、說明分頁）
- **Splatoon**：主設定介面（`Gui/`）完成中文化；個別副本疊加點腳本（`SplatoonScripts/`）仍在進行中，規模較大，將分批持續翻譯
- **BossmodReborn**：團隊戰術設定選項（含高難度副本站位/機制處理選項）完成中文化，戰鬥錄影分析工具（`Replay/`）尚未翻譯
- **SubmarineTracker**：修正既有翻譯資源檔誤植為簡體中文的問題，統一轉換為繁體中文
- **ChatTwo**：修正既有翻譯資源檔簡繁混雜問題
- **LazyLoot**：修正伺服器資訊列（DTR）狀態文字未翻譯的問題
- **AutoRetainer**：補完潛水艇工坊、派遣任務規劃器等日常功能的翻譯

### 已知未完成項目

- Splatoon 的個別副本疊加點腳本（`SplatoonScripts/`）— 規模龐大，分批處理中
- BossmodReborn 的戰鬥錄影分析工具（`Replay/`）— 開發/進階玩家工具，優先度較低
- InventoryTools、Dynamis、visland、vnavmesh 的部分深層設定/除錯介面 — 刻意保留原文，屬低可見度或開發工具性質
