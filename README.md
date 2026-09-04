# DalamudPluginsTC

台服（TC，Traditional Chinese client）專用的第三方 Dalamud 插件庫，收錄並持續維護 **70 個**對應台服客戶端的插件 fork。

台服目前使用的 Dalamud 為 **API Level 13**，而多數插件的官方版本已隨國際服前進到更新的 API 等級，無法直接在台服安裝。本倉庫維護這些插件對應 **TC 7.20（API13）** 的相容分支，並**已完成全面繁體中文化**（介面採台服官方譯名、對照官方遊戲資料表校訂）。

## 使用方式

1. 開啟遊戲內插件安裝器
2. 進入「實驗性功能」（Experimental）
3. 在「自訂插件庫」（Custom Plugin Repositories）加入以下網址：

```
https://raw.githubusercontent.com/ffxiv-tc-port/DalamudPluginsTC/main/repo.json
```

4. 儲存後即可在插件安裝器中搜尋並安裝下方列出的插件

## 收錄插件

插件名稱連至本 org 維護的 fork（台服相容性問題請開在這裡），「上游」為原始專案。不保證插件彼此之間的相容性，請自行評估風險後使用。

### 戰鬥與副本

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Bossmod Reborn**](https://github.com/ffxiv-tc-port/BossmodReborn) | [FFXIV-CombatReborn](https://github.com/FFXIV-CombatReborn/BossModReborn) | 副本機制提示、時間軸與自動閃避，不再因機制失誤 |
| [**Wrath Combo**](https://github.com/ffxiv-tc-port/WrathCombo) | [PunishXIV](https://github.com/PunishXIV/WrathCombo) | 一鍵連擊／整合連段 |
| [**Splatoon**](https://github.com/ffxiv-tc-port/Splatoon) | [PunishXIV](https://github.com/PunishXIV/Splatoon) | 場景繪製點線面／腳本化機制標示 |
| [**Avarice**](https://github.com/ffxiv-tc-port/Avarice) | [PunishXIV](https://github.com/PunishXIV/Avarice) | 身位判定即時回饋（背面／側面追蹤、距離指示） |
| [**Pixel Perfect**](https://github.com/ffxiv-tc-port/PixelPerfect) | [Haplo064](https://github.com/Haplo064/PixelPerfect) | 顯示碰撞判定範圍／站位輔助 |
| [**LazyLoot**](https://github.com/ffxiv-tc-port/LazyLoot) | [PunishXIV](https://github.com/PunishXIV/LazyLoot) | 打怪但懶得選骰的自動擲骰插件 |
| [**IINACT**](https://github.com/ffxiv-tc-port/IINACT) | [marzent](https://github.com/marzent/IINACT) | 免 ACT 的戰鬥數據解析（內建 Overlay Plugin 移植版，TC opcodes 由 PlusoneChiang 維護） |
| [**Crossingway**](https://github.com/ffxiv-tc-port/Crossingway) | [Styr1x/Browsingway](https://github.com/Styr1x/Browsingway) | 遊戲內 Chromium 瀏覽器覆蓋層（可搭配 IINACT 顯示戰鬥數據、Cactbot 時間軸等） |
| [**Big Player Debuffs**](https://github.com/ffxiv-tc-port/BigPlayerDebuffs) | [rgd87](https://github.com/rgd87/BigPlayerDebuffs) | 放大自己施加在目標上的強化／弱化狀態圖示 |
| [**EnemyListDebuffs**](https://github.com/ffxiv-tc-port/EnemyListDebuffs) | [aers](https://github.com/aers/EnemyListDebuffs) | 在敵對列表顯示你施加的弱化狀態／持續傷害圖示 |
| [**WaymarkPresetPlugin**](https://github.com/ffxiv-tc-port/WaymarkPresetPlugin) | [PunishedPineapple](https://github.com/PunishedPineapple/WaymarkPresetPlugin) | 儲存、編輯、放置場地標點預設 |
| [**Death Recap**](https://github.com/ffxiv-tc-port/ffxiv-deathrecap) | [Kouzukii](https://github.com/Kouzukii/ffxiv-deathrecap) | 死亡回顧：是什麼殺了你 |

### 狩獵與特殊場域

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Hunt Helper**](https://github.com/ffxiv-tc-port/HuntHelper) | [img02](https://github.com/img02/HuntHelper) | 狩獵任務雷達＋車隊記錄器＋S 級討伐輔助 |
| [**Sonar**](https://github.com/ffxiv-tc-port/SonarPlugin) | [FFXIV-Sonar](https://github.com/FFXIV-Sonar/SonarDistrib) | 自動傳送與接收狩獵任務及 FATE 情報 |
| [**Eureka Helper**](https://github.com/ffxiv-tc-port/EurekaHelper) | [KangasZ](https://github.com/KangasZ/EurekaHelper) | 優雷卡追蹤器與多項實用功能整合 |
| [**Logogram Helper**](https://github.com/ffxiv-tc-port/LogogramHelper) | [apetih](https://github.com/apetih/LogogramHelper) | 優雷卡文理技能輔助 |
| [**NecroLens**](https://github.com/ffxiv-tc-port/NecroLens) | [Jukkales](https://github.com/Jukkales/NecroLens) | 深層迷宮輔助（怪物雷達等） |
| [**MonsterDex**](https://github.com/ffxiv-tc-port/MonsterDex) | [wolfcomp](https://github.com/wolfcomp/MonsterDex) | 深層迷宮怪物圖鑑（可暈眩／弱點等即時資訊） |
| [**Palace Pal**](https://github.com/ffxiv-tc-port/PalacePal) | [PunishXIV](https://github.com/PunishXIV/PalacePal) | 深層迷宮陷阱與寶藏標示（需搭配 Splatoon） |
| [**BOCCHI**](https://github.com/ffxiv-tc-port/BOCCHI) | [OhKannaDuh](https://github.com/OhKannaDuh/BOCCHI) | 蜃景新月島輔助介面（台服尚未開放該內容） |

### 採集與製作

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Artisan**](https://github.com/ffxiv-tc-port/Artisan) | [PunishXIV](https://github.com/PunishXIV/Artisan) | 生產小精靈，全能製作插件 |
| [**GatherBuddy Reborn**](https://github.com/ffxiv-tc-port/GatherbuddyReborn) | [FFXIV-CombatReborn](https://github.com/FFXIV-CombatReborn/GatherBuddyReborn) | 把採集與釣魚簡化到極致 |
| [**AutoHook**](https://github.com/ffxiv-tc-port/AutoHook) | [PunishXIV](https://github.com/PunishXIV/AutoHook) | 自動甩竿／釣魚自動化 |
| [**Chilled Leves**](https://github.com/ffxiv-tc-port/ChilledLeves) | [LeontopodiumNivale14](https://github.com/LeontopodiumNivale14/ChilledLeves) | 理符任務，冰鎮上桌 |
| [**Ice's Cosmic Exploration**](https://github.com/ffxiv-tc-port/ICE) | [LeontopodiumNivale14](https://github.com/LeontopodiumNivale14/Ices-Cosmic-Exploration) | 宇宙探索的自動化好夥伴 |
| [**visland**](https://github.com/ffxiv-tc-port/visland) | [awgil](https://github.com/awgil/ffxiv_visland) | 無人島自動化（牧場／耕地／開拓工坊／採集） |
| [**Explorer's Icebox**](https://github.com/ffxiv-tc-port/Explorers-Icebox) | [LeontopodiumNivale14](https://github.com/LeontopodiumNivale14/Explorers-Icebox) | 配合 vnavmesh／visland 的無人島採集自動化 |

### 任務與移動

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Questionable**](https://github.com/ffxiv-tc-port/Questionable) | [PunishXIV](https://github.com/PunishXIV/Questionable) | 自動做任務（主線／支線） |
| [**GatheringPathRenderer**](https://github.com/ffxiv-tc-port/Questionable) | [PunishXIV](https://github.com/PunishXIV/Questionable) | Questionable 附屬開發插件，繪製採集點位視覺化 |
| [**TextAdvance**](https://github.com/ffxiv-tc-port/TextAdvance) | [NightmareXIV](https://github.com/NightmareXIV/TextAdvance) | 自動跳過對話、確認過場跳過、快速接交任務 |
| [**AutoDuty**](https://github.com/ffxiv-tc-port/AutoDuty) | [ffxivcode](https://github.com/ffxivcode/AutoDuty) | 自動跑本，解放雙手 |
| [**YesAlready**](https://github.com/ffxiv-tc-port/YesAlready) | [PunishXIV](https://github.com/PunishXIV/YesAlready) | 自動點擊你指定的各種確認對話框 |
| [**Something Need Doing**](https://github.com/ffxiv-tc-port/SomethingNeedDoing) | [Jaksuhn](https://github.com/Jaksuhn/SomethingNeedDoing) | 巨集擴展／自動化腳本引擎 |
| [**Lifestream**](https://github.com/ffxiv-tc-port/Lifestream) | [NightmareXIV](https://github.com/NightmareXIV/Lifestream) | 乙太之光傳送與跨世界旅行加速 |
| [**vnavmesh**](https://github.com/ffxiv-tc-port/vnavmesh) | [awgil](https://github.com/awgil/ffxiv_navmesh) | 自動尋路／移動 |
| [**Skip Cutscene**](https://github.com/ffxiv-tc-port/SkipCutscene) | [KangasZ](https://github.com/KangasZ/SkipCutscene) | 跳過任務輪盤中強制播放的過場動畫 |
| [**Zodiac Buddy**](https://github.com/ffxiv-tc-port/ZodiacBuddy) | [foophoof](https://github.com/foophoof/ZodiacBuddy) | 舊版古武（神兵）任務助手：傳送捷徑與魔典目標定位 |

### 金碟與小遊戲

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Saucy**](https://github.com/ffxiv-tc-port/Saucy) | [PunishXIV](https://github.com/PunishXIV/Saucy) | 當賺取 MGP 太費工夫的時候 |
| [**TriadBuddy**](https://github.com/ffxiv-tc-port/FFTriadBuddyDalamud) | [MgAl2O4](https://github.com/MgAl2O4/FFTriadBuddyDalamud) | 九宮幻卡解牌助手 |
| [**LatihasChocobo**](https://github.com/ffxiv-tc-port/LatihasChocobo) | [Latihas](https://github.com/Latihas/LatihasChocobo) | 自動陸行鳥競賽 |
| [**Avant-Garde**](https://github.com/ffxiv-tc-port/AvantGarde) | [NeNeppie](https://github.com/NeNeppie/AvantGarde) | 時尚品鑑小遊戲的符合條件裝備提示 |
| [**Easier Faux Hollows**](https://github.com/ffxiv-tc-port/vfaux) | [awgil](https://github.com/awgil/vfaux) | 以已知圖樣求解幻巧拼圖 |
| [**ezWondrousTails**](https://github.com/ffxiv-tc-port/EzWondrousTails) | [daemitus](https://github.com/daemitus/WondrousTailsSolver) | 在天書奇談面板顯示連線機率與「重新貼」的期望值 |

### 雇員與市場

| 插件 | 上游 | 說明 |
|------|------|------|
| [**AutoRetainer**](https://github.com/ffxiv-tc-port/AutoRetainer) | [PunishXIV](https://github.com/PunishXIV/AutoRetainer) | 躺著也能派遣雇員出去探險 |
| [**Submarine Tracker**](https://github.com/ffxiv-tc-port/SubmarineTracker) | [Infiziert90](https://github.com/Infiziert90/SubmarineTracker) | 潛水艇配置追蹤與建構器 |
| [**Marketbuddy**](https://github.com/ffxiv-tc-port/Marketbuddy) | [PunishXIV](https://github.com/PunishXIV/Marketbuddy) | 少點幾下，多賺點 Gil！市場板快速掛賣 |
| [**Price Insight**](https://github.com/ffxiv-tc-port/PriceInsight) | [Kouzukii](https://github.com/Kouzukii/ffxiv-priceinsight) | 滑鼠懸停顯示道具市場公告板價格 |
| [**Item Vendor Location**](https://github.com/ffxiv-tc-port/ItemVendorLocation) | [electr0sheep](https://github.com/electr0sheep/ItemVendorLocation) | 顯示可購買該道具的商人所在位置 |
| [**Accountant**](https://github.com/ffxiv-tc-port/Accountant) | [Ottermandias](https://github.com/Ottermandias/Accountant) | 角色任務計時器（雇員／潛艇／園圃等） |

### 物品與介面

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Allagan Tools**](https://github.com/ffxiv-tc-port/InventoryTools) | [Critical-Impact](https://github.com/Critical-Impact/InventoryTools) | 全帳號物品追蹤、收納定位與製作規劃（InventoryTools） |
| [**Gearsetter**](https://github.com/ffxiv-tc-port/Gearsetter) | [VeraNala](https://github.com/VeraNala/Gearsetter) | 查詢各職業可換裝的裝備升級 |
| [**Chat 2**](https://github.com/ffxiv-tc-port/ChatTwo) | [Infiziert90](https://github.com/Infiziert90/ChatTwo) | 全新的聊天視窗 |
| [**QoL Bar**](https://github.com/ffxiv-tc-port/QoLBar) | [UnknownX7](https://github.com/UnknownX7/QoLBar) | 可自訂的 ImGui 快捷列 |
| [**DailyDuty**](https://github.com/ffxiv-tc-port/DailyDuty) | [MidoriKami](https://github.com/MidoriKami/DailyDuty) | 輕鬆追蹤每日／每週任務 |
| [**NotificationMaster**](https://github.com/ffxiv-tc-port/NotificationMaster) | [NightmareXIV](https://github.com/NightmareXIV/NotificationMaster) | 遊戲視窗縮小時的各類事件通知 |
| [**Mini-Mappingway**](https://github.com/ffxiv-tc-port/MiniMappingway) | [jaycewhite](https://github.com/jaycewhite/MiniMappingway) | 在小地圖上顯示好友與公會成員 |
| [**Character Panel Refined**](https://github.com/ffxiv-tc-port/CharacterPanelRefined) | [Kouzukii](https://github.com/Kouzukii/ffxiv-characterstatus-refined) | 精簡角色面板，顯示暴擊率等實用數值 |
| [**XIV 藏寶圖工具小幫手**](https://github.com/ffxiv-tc-port/XivTreasureParty) | [cycleapple](https://github.com/cycleapple/xiv-party-treasure-helper) | 寶物地圖組隊協作，與網頁版即時共享房間 |
| [**TC Toolbox**](https://github.com/ffxiv-tc-port/TCToolbox) | 自製 | 台服雜項 QoL 模組集：合建交料、餵鳥、QTE、周邊玩家、園圃自動化、批次僱員改名 |
| [**Mappy**](https://github.com/ffxiv-tc-port/Mappy) | [harbingerftw](https://github.com/harbingerftw/Mappy) | ImGui 重繪並取代原生地圖視窗的全功能地圖 |
| [**SortaKinda**](https://github.com/ffxiv-tc-port/SortaKinda) | [MidoriKami](https://github.com/MidoriKami/SortaKinda) | 依規則把背包道具排進指定槽位，取代原生 /isort |
| [**Peeping Tom**](https://github.com/ffxiv-tc-port/PeepingTom) | [Caraxi](https://github.com/Caraxi/PeepingTom) | 顯示誰正在指向你（可選聊天記錄與音效提示） |
| [**Better Mount Roulette**](https://github.com/ffxiv-tc-port/BetterMountRoulette) | [CMDRNuffin](https://github.com/CMDRNuffin/BetterMountRoulette) | 從自訂坐騎分組隨機召喚 |
| [**High FPS Physics**](https://github.com/ffxiv-tc-port/xivlauncher_physics_plugin) | [ItsLunaYup](https://github.com/ItsLunaYup/xivlauncher_physics_plugin) | 修正高幀率下的布料／頭髮物理模擬 |
| [**ScrollableTabs**](https://github.com/ffxiv-tc-port/ScrollableTabs) | [Haselnussbomber](https://github.com/Haselnussbomber/ScrollableTabs) | 用滑鼠滾輪切換背包、軍儲等視窗的分頁 |
| [**Brio**](https://github.com/ffxiv-tc-port/Brio) | [Etheirys](https://github.com/Etheirys/Brio) | 集體動作（GPose）角色姿勢、表情與場景編輯 |
| [**TataruPraise**](https://github.com/ffxiv-tc-port/TataruPraise) | 自製 | 塔塔露誇獎語音：各插件完成事件時播放合成短句 |

### 開發工具

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Dynamis**](https://github.com/ffxiv-tc-port/Dynamis) | [Exter-N](https://github.com/Exter-N/Dynamis) | 開發／除錯／逆向工程工具與實驗工作台 |
| [**Dynamis (with Hosted PowerShell)**](https://github.com/ffxiv-tc-port/Dynamis) | [Exter-N](https://github.com/Exter-N/Dynamis) | Dynamis 含內建 PowerShell 版本 |
| [**Meddle**](https://github.com/ffxiv-tc-port/Meddle) | [PassiveModding](https://github.com/PassiveModding/Meddle) | 匯出角色／NPC 模型為 glTF（開發用途） |

## 為什麼需要這個倉庫？

台服使用的 Dalamud 版本與官方插件倉庫所要求的 API 等級不一致，導致許多實用插件無法直接安裝。本倉庫維護這些插件對應 API13 的相容版本，並修正因客戶端差異導致的問題——包括記憶體結構位移、語言判斷、台服資料表差異（NPC／道具 ID 不同、未開放內容）等。

## 繁體中文化

所有收錄插件的介面均已翻譯為繁體中文：

- **翻譯外部化**：程式碼保留英文原文作為鍵值，譯文存放於外部語言檔（ini／resx／json），跟隨上游更新時翻譯不會遺失
- 修正了多個台服客戶端語言判斷問題（部分插件先前因此永遠顯示英文）

## 貢獻 / 回報問題

插件在台服上的相容性問題，請至上表對應的 **ffxiv-tc-port fork** 開 issue（上游專案不處理台服相容性）。翻譯用語建議也歡迎回報。
