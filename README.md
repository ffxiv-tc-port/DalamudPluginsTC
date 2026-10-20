# DalamudPluginsTC

台服（TC，Traditional Chinese client）專用的第三方 Dalamud 插件庫，收錄並持續維護 **72 個**對應台服客戶端的插件（來自 70 個 fork repo）。

台服目前使用的 Dalamud 為 **API Level 13**，而多數插件的官方版本已隨國際服前進到更新的 API 等級，無法直接在台服安裝。本倉庫維護這些插件對應 **TC 7.25（API13）** 的相容分支，並**已完成全面繁體中文化**（介面採台服官方譯名、對照官方遊戲資料表校訂）。

## 使用方式

1. 開啟遊戲內插件安裝器
2. 進入「實驗性功能」（Experimental）
3. 在「自訂插件庫」（Custom Plugin Repositories）加入以下網址：

```
https://raw.githubusercontent.com/ffxiv-tc-port/DalamudPluginsTC/main/repo.json
```

4. 儲存後即可在插件安裝器中搜尋並安裝下方列出的插件

> 📌 2026-09-17 起本倉庫與收錄插件皆為 private（只服務維護者本人）：這個網址、
> 圖示與插件下載都需要驗證，要在 Dalamud 設定頁填入 GitHub fine-grained token
> （本 org 的 Dalamud fork 才有這個設定）才能抓取。設計文件在維護機的艦隊根目錄
> `D:/ffxiv-tc-port/docs/private-migration-design.md`（不在本倉庫）。

## 收錄插件

插件名稱連至本 org 維護的 fork（台服相容性問題請開在這裡），「上游」為原始專案。不保證插件彼此之間的相容性，請自行評估風險後使用。

### 戰鬥與副本

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Bossmod Reborn**](https://github.com/ffxiv-tc-port/BossmodReborn) | [FFXIV-CombatReborn](https://github.com/FFXIV-CombatReborn/BossModReborn) | 首領戰雷達與機制提示、自動循環、冷卻計劃，以及依機制自動閃避的 AI；也支援深層迷宮的自動探索與小地圖。 |
| [**Wrath Combo**](https://github.com/ffxiv-tc-port/WrathCombo) | [PunishXIV](https://github.com/PunishXIV/WrathCombo) | 將連段技能與互斥技能整合到單一按鍵，並提供額外的自動戰鬥輔助功能。 |
| [**Splatoon**](https://github.com/ffxiv-tc-port/Splatoon) | [PunishXIV](https://github.com/PunishXIV/Splatoon) | 在遊戲世界中疊加自訂範圍、路標與文字提示，可依條件顯示，並支援自訂腳本；本身不附預設內容。 |
| [**Avarice**](https://github.com/ffxiv-tc-port/Avarice) | [PunishXIV](https://github.com/PunishXIV/Avarice) | 站位輔助：以像素級精度即時顯示目標的背面／側面判定範圍與技能距離指示。 |
| [**Pixel Perfect**](https://github.com/ffxiv-tc-port/PixelPerfect) | [Haplo064](https://github.com/Haplo064/PixelPerfect) | 在角色身上畫出自訂的圓環、直線、扇形等範圍指示，可依職業與戰鬥狀態顯示。 |
| [**LazyLoot**](https://github.com/ffxiv-tc-port/LazyLoot) | [PunishXIV](https://github.com/PunishXIV/LazyLoot) | 用指令或全自動處理戰利品擲骰（需求／貪婪／放棄），可依道具與任務自訂規則。 |
| [**IINACT**](https://github.com/ffxiv-tc-port/IINACT) | [marzent](https://github.com/marzent/IINACT) | 在 Dalamud 內執行 FFXIV_ACT_Plugin 解析戰鬥數據，提供 ACT／OverlayPlugin 相容的 WebSocket 給疊加層使用，不需安裝 ACT。 |
| [**Crossingway**](https://github.com/ffxiv-tc-port/Crossingway) | [Styr1x/Browsingway](https://github.com/Styr1x/Browsingway) | 在遊戲內渲染 Chromium 瀏覽器疊加層，可顯示 ACT／IINACT 戰鬥數據、Cactbot 時間軸或任意網頁。 |
| [**Big Player Debuffs**](https://github.com/ffxiv-tc-port/BigPlayerDebuffs) | [rgd87](https://github.com/rgd87/BigPlayerDebuffs) | 放大目標身上由自己施加的強化／弱化狀態圖示。 |
| [**EnemyListDebuffs**](https://github.com/ffxiv-tc-port/EnemyListDebuffs) | [aers](https://github.com/aers/EnemyListDebuffs) | 在敵對列表顯示你施加的弱化狀態與剩餘時間。 |
| [**WaymarkPresetPlugin**](https://github.com/ffxiv-tc-port/WaymarkPresetPlugin) | [PunishedPineapple](https://github.com/PunishedPineapple/WaymarkPresetPlugin) | 儲存、編輯、放置場地標記預設集，可在地圖上調整座標、匯入匯出，也可設定進副本時自動載入。 |
| [**Death Recap**](https://github.com/ffxiv-tc-port/ffxiv-deathrecap) | [Kouzukii](https://github.com/Kouzukii/ffxiv-deathrecap) | 死亡回顧：顯示自己或隊友死前受到的傷害、治療與狀態變化，看看是什麼殺了你。 |

### 狩獵與特殊場域

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Hunt Helper**](https://github.com/ffxiv-tc-port/HuntHelper) | [img02](https://github.com/img02/HuntHelper) | 狩獵雷達地圖與重生點、狩獵列車記錄（可匯出匯入）、聊天與語音通知，以及 S 階級觸發計數器。 |
| [**Sonar**](https://github.com/ffxiv-tc-port/SonarPlugin) | [FFXIV-Sonar](https://github.com/FFXIV-Sonar/SonarDistrib) | 偵測並提醒危險標的與 F.A.T.E.；打開「連線 Sonar 伺服器」（預設關）後可與其他 Sonar 使用者互相分享回報。 |
| [**Eureka Helper**](https://github.com/ffxiv-tc-port/EurekaHelper) | [KangasZ](https://github.com/KangasZ/EurekaHelper) | 禁地優雷卡的 NM 追蹤器、天氣時間與鬧鐘、聖靈與尋寶輔助，也支援新月島的魔法甕尋寶。 |
| [**Logogram Helper**](https://github.com/ffxiv-tc-port/LogogramHelper) | [apetih](https://github.com/apetih/LogogramHelper) | 開啟優雷卡文理融合器時，列出各文理技能的配方與可合成數量，並可一鍵填入文理碎晶。 |
| [**NecroLens**](https://github.com/ffxiv-tc-port/NecroLens) | [Jukkales](https://github.com/Jukkales/NecroLens) | 深層迷宮 ESP 透視：顯示怪物的感知範圍與朝向，以及寶箱、陷阱、傳送裝置等物件。 |
| [**MonsterDex**](https://github.com/ffxiv-tc-port/MonsterDex) | [wolfcomp](https://github.com/wolfcomp/MonsterDex) | 深層迷宮魔物圖鑑：即時顯示魔物的仇恨類型、威脅程度、狀態弱點與重要機制概覽。原名 DeepDungeonDex。 |
| [**Palace Pal**](https://github.com/ffxiv-tc-port/PalacePal) | [PunishXIV](https://github.com/PunishXIV/PalacePal) | 在死者宮殿、天之御柱、正統優雷卡標示可能的陷阱與受詛咒藏寶箱位置，社群資料只下載不上傳。需搭配 Splatoon。 |
| [**HoardFarm**](https://github.com/ffxiv-tc-port/HoardFarm) | [Jukkales](https://github.com/Jukkales/HoardFarm) | 在天之御柱自動反覆挖埋藏的寶藏（手動開始、預設關閉、可急停）。需搭配 vnavmesh。 |
| [**BOCCHI**](https://github.com/ffxiv-tc-port/BOCCHI) | [OhKannaDuh](https://github.com/OhKannaDuh/BOCCHI) | 蜃景幻界新月島輔助：F.A.T.E.／緊急遭遇戰面板與提醒、寶箱與幸運胡蘿蔔雷達，以及手動開始、可急停的島上自動化。 |

### 採集與製作

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Artisan**](https://github.com/ffxiv-tc-port/Artisan) | [PunishXIV](https://github.com/PunishXIV/Artisan) | 自動製作插件：用內建求解器（含 Raphael）自動執行製作技能，支援製作清單、耐力模式、公會工坊與從僱員補貨。 |
| [**GatherBuddy Reborn**](https://github.com/ffxiv-tc-port/GatherbuddyReborn) | [FFXIV-CombatReborn](https://github.com/FFXIV-CombatReborn/GatherBuddyReborn) | 採集與釣魚輔助：依物品名稱找採集點與釣點、提醒限時採集點與天氣，搭配 vnavmesh 可全自動採集。 |
| [**AutoHook**](https://github.com/ffxiv-tc-port/AutoHook) | [PunishXIV](https://github.com/PunishXIV/AutoHook) | 全自動釣魚：依魚種、釣餌與狀態設定提鉤方式，自動施放釣魚技能，並支援自動刺魚。 |
| [**Chilled Leves**](https://github.com/ffxiv-tc-port/ChilledLeves) | [LeontopodiumNivale14](https://github.com/LeontopodiumNivale14/ChilledLeves) | 理符任務輔助：排好要做的理符，自動前往 NPC 領取與交納；另有採集職理符刷取與受理限額提醒。需搭配 vnavmesh。 |
| [**Ice's Cosmic Exploration**](https://github.com/ffxiv-tc-port/ICE) | [LeontopodiumNivale14](https://github.com/LeontopodiumNivale14/Ices-Cosmic-Exploration) | 宇宙探索自動化：自動接取與回報任務，搭配 Artisan、vnavmesh、AutoHook 完成製作、採集與釣魚；機甲行動只顯示資訊。 |
| [**visland**](https://github.com/ffxiv-tc-port/visland) | [awgil](https://github.com/awgil/ffxiv_visland) | 無人島開拓自動化：錄製／播放採集路線、耕地與牧場領取、屯貨倉庫與產品交易、開拓工坊排程，另有缺料總表。 |
| [**Explorer's Icebox**](https://github.com/ffxiv-tc-port/Explorers-Icebox) | [LeontopodiumNivale14](https://github.com/LeontopodiumNivale14/Explorers-Icebox) | 無人島採集自動化：跑內建路線刷開拓經驗或採集素材，快滿時自動到產品交易出售。需搭配 vnavmesh。 |

### 任務與移動

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Questionable**](https://github.com/ffxiv-tc-port/Questionable) | [PunishXIV](https://github.com/PunishXIV/Questionable) | 自動接取並完成任務：導航到任務地點、處理對話與互動，戰鬥與任務戰鬥可交給 BossMod。需搭配 vnavmesh、TextAdvance 與 Lifestream。 |
| [**GatheringPathRenderer**](https://github.com/ffxiv-tc-port/Questionable) | [PunishXIV](https://github.com/PunishXIV/Questionable) | Questionable 附屬開發插件，繪製採集點位視覺化 |
| [**TextAdvance**](https://github.com/ffxiv-tc-port/TextAdvance) | [NightmareXIV](https://github.com/NightmareXIV/TextAdvance) | 任務推進輔助：自動確認任務接取與交付、跳過字幕與過場動畫，並可選用 Splatoon 顯示任務目標指示、用 vnavmesh 自動走到任務地點。 |
| [**AutoDuty**](https://github.com/ffxiv-tc-port/AutoDuty) | [ffxivcode](https://github.com/ffxivcode/AutoDuty) | 自動幫你跑副本，支援親信戰友、劇情輔助器、冒險者分隊、多變迷宮或一般模式。需搭配 BossModReborn、vnavmesh 與一款戰鬥循環插件（WrathCombo 或 BossMod AutoRotation）使用。 |
| [**YesAlready**](https://github.com/ffxiv-tc-port/YesAlready) | [PunishXIV](https://github.com/PunishXIV/YesAlready) | 依你設定的文字（可用正規表示式）自動點選確認框、選單與對話，並內建分解、僱員派遣、任務準備完成確認等常見視窗的自動處理。 |
| [**Something Need Doing**](https://github.com/ffxiv-tc-port/SomethingNeedDoing) | [Jaksuhn](https://github.com/Jaksuhn/SomethingNeedDoing) | 進階巨集與腳本執行工具：可撰寫、儲存並執行超越遊戲內建限制的巨集與 Lua 腳本，不只用於生產。 |
| [**Lifestream**](https://github.com/ffxiv-tc-port/Lifestream) | [NightmareXIV](https://github.com/NightmareXIV/Lifestream) | 一個指令跨世界、資料中心、分線與住宅區傳送；也可點擊地圖上的城內乙太之晶快速傳送，或自動走／飛到自訂落點。 |
| [**vnavmesh**](https://github.com/ffxiv-tc-port/vnavmesh) | [awgil](https://github.com/awgil/ffxiv_navmesh) | 建構地圖導航網格並自動尋路移動角色（地面與飛行），供 Questionable、AutoDuty 等插件透過 IPC 呼叫；不會替你上坐騎。 |
| [**Skip Cutscene**](https://github.com/ffxiv-tc-port/SkipCutscene) | [KangasZ](https://github.com/KangasZ/SkipCutscene) | 幫助你跳過「隨機任務：主線任務」中 50 級三個副本強制播放的過場動畫。 |
| [**Zodiac Buddy**](https://github.com/ffxiv-tc-port/ZodiacBuddy) | [foophoof](https://github.com/foophoof/ZodiacBuddy) | 2.0 傳說武器（黃道武器）製作助手：任務手冊目標定位與傳送捷徑、新星／黃道勇士進度視窗。 |

### 金碟與小遊戲

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Saucy**](https://github.com/ffxiv-tc-port/Saucy) | [PunishXIV](https://github.com/PunishXIV/Saucy) | 金碟遊樂園小遊戲自動化：九宮幻卡、仙人微彩、仙人仙彩、GATE 與孤樹無援，另有金碟遊樂園導航。 |
| [**TriadBuddy**](https://github.com/ffxiv-tc-port/FFTriadBuddyDalamud) | [MgAl2O4](https://github.com/MgAl2O4/FFTriadBuddyDalamud) | 九宮幻卡解牌助手：標示最佳落子、依對手 NPC 最佳化卡組、找出還沒收集的卡片（僅支援 NPC 對戰）。 |
| [**LatihasChocobo**](https://github.com/ffxiv-tc-port/LatihasChocobo) | [Latihas](https://github.com/Latihas/LatihasChocobo) | 陸行鳥競賽自動化：自動報名、操作跑完賽事並使用道具與技能，逐場學習賽道。 |
| [**Avant-Garde**](https://github.com/ffxiv-tc-port/AvantGarde) | [NeNeppie](https://github.com/NeNeppie/AvantGarde) | 在時尚品鑑的提示旁列出符合條件的裝備，標示是否已擁有與取得來源。 |
| [**Easier Faux Hollows**](https://github.com/ffxiv-tc-port/vfaux) | [awgil](https://github.com/awgil/vfaux) | 幻巧拼圖解謎輔助：比對已知圖樣，直接在遊戲面板上標出雙劍、寶箱、狐狸的位置與最佳下一格。 |
| [**ezWondrousTails**](https://github.com/ffxiv-tc-port/EzWondrousTails) | [daemitus](https://github.com/daemitus/WondrousTailsSolver) | 在天書奇談面板上顯示貼出 1／2／3 條故事線的機率，以及「重新貼」之後的平均機率。 |

### 僱員與市場

| 插件 | 上游 | 說明 |
|------|------|------|
| [**AutoRetainer**](https://github.com/ffxiv-tc-port/AutoRetainer) | [PunishXIV](https://github.com/PunishXIV/AutoRetainer) | 自動收取並重新指派僱員探險與潛水艇／飛空艇遠航探索，支援多角色輪流處理與大國防聯軍籌備稀有品。 |
| [**Submarine Tracker**](https://github.com/ffxiv-tc-port/SubmarineTracker) | [Infiziert90](https://github.com/Infiziert90/SubmarineTracker) | 追蹤潛水艇配置、航線與所需時間，內含配置建構器與經驗值最佳化、返航／維修通知、戰利品總覽與歷史紀錄，並支援多個公會。 |
| [**Marketbuddy**](https://github.com/ffxiv-tc-port/Marketbuddy) | [PunishXIV](https://github.com/PunishXIV/Marketbuddy) | 市場佈告板上架、改價、下架與跨世界比價的操作輔助；所有市場動作都要你手動觸發。 |
| [**Price Insight**](https://github.com/ffxiv-tc-port/PriceInsight) | [Kouzukii](https://github.com/Kouzukii/ffxiv-priceinsight) | 當你將滑鼠移到道具上時，會在下方顯示該道具的普通品／高品質市場佈告板價格，以及最近一次的成交紀錄。 |
| [**Item Vendor Location**](https://github.com/ffxiv-tc-port/ItemVendorLocation) | [electr0sheep](https://github.com/electr0sheep/ItemVendorLocation) | 道具右鍵選單可查詢有哪些 NPC 商人在賣，並可交給 Lifestream 帶你前往。 |
| [**Accountant**](https://github.com/ffxiv-tc-port/Accountant) | [Ottermandias](https://github.com/Ottermandias/Accountant) | 記錄多名角色的僱員、飛空艇、潛水艇、乙太轉輪與作物計時器，以及其他各種雜項任務進度。 |
| [**GilDelta**](https://github.com/ffxiv-tc-port/GilDelta) | [yagi2](https://github.com/yagi2/GilDelta) | 追蹤自己、僱員與公會儲物櫃的金幣流向，每筆變動自動分類，附常駐小工具、儀表板與每日結算。 |

### 物品與介面

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Allagan Tools**](https://github.com/ffxiv-tc-port/InventoryTools) | [Critical-Impact](https://github.com/Critical-Impact/InventoryTools) | Allagan Tools：追蹤所有角色、僱員與公會倉庫的道具，提供篩選清單、製作規劃、市場佈告板查價與各種資料查詢視窗。 |
| [**Gearsetter**](https://github.com/ffxiv-tc-port/Gearsetter) | [VeraNala](https://github.com/VeraNala/Gearsetter) | 瀏覽各職業可以裝備的所有裝備，並列出你目前套裝還能升級的部位。 |
| [**Chat 2**](https://github.com/ffxiv-tc-port/ChatTwo) | [Infiziert90](https://github.com/Infiziert90/ChatTwo) | 完整重寫遊戲內建聊天視窗：無限分頁、彈性過濾、可搜尋的聊天記錄資料庫。 |
| [**QoL Bar**](https://github.com/ffxiv-tc-port/QoLBar) | [UnknownX7](https://github.com/UnknownX7/QoLBar) | 自訂按鈕列：把聊天指令、巨集與其他插件的介面放在隨時可按的列上，不必佔用快捷列。 |
| [**DailyDuty**](https://github.com/ffxiv-tc-port/DailyDuty) | [MidoriKami](https://github.com/MidoriKami/DailyDuty) | 追蹤每日與每週任務的完成狀況並提醒，部分項目可一鍵帶你前往。 |
| [**NotificationMaster**](https://github.com/ffxiv-tc-port/NotificationMaster) | [NightmareXIV](https://github.com/NightmareXIV/NotificationMaster) | 在遊戲視窗縮到最小時，通知你各種遊戲內事件。 |
| [**Mini-Mappingway**](https://github.com/ffxiv-tc-port/MiniMappingway) | [jaycewhite](https://github.com/jaycewhite/MiniMappingway) | 在小地圖上標示好友、公會成員與附近玩家，可自訂標記樣式；另有 PvP 玩家雷達。 |
| [**Character Panel Refined**](https://github.com/ffxiv-tc-port/CharacterPanelRefined) | [Kouzukii](https://github.com/Kouzukii/ffxiv-characterstatus-refined) | 精簡角色面板，加入暴擊與直擊機率、預估輸出與裝備屬性合計，方便比較裝備。 |
| [**XIV 寶物地圖工具小幫手**](https://github.com/ffxiv-tc-port/XivTreasureParty) | [cycleapple](https://github.com/cycleapple/xiv-party-treasure-helper) | 寶物地圖協作工具：與網頁版使用者混合組隊、即時同步同一份清單，可開地圖標旗、發送座標連結，並可交給 Lifestream 帶路。 |
| [**TC Toolbox**](https://github.com/ffxiv-tc-port/TCToolbox) | 自製 | 台服自製的整合工具箱：背包整理、戰鬥與招募、公會與園圃、介面雜項等小功能模組，各自開關、預設全部關閉。 |
| [**Mappy**](https://github.com/ffxiv-tc-port/Mappy) | [harbingerftw](https://github.com/harbingerftw/Mappy) | 以 ImGui 重繪並取代原生地圖視窗的全功能地圖，可顯示其他插件的標記與路線。 |
| [**SortaKinda**](https://github.com/ffxiv-tc-port/SortaKinda) | [MidoriKami](https://github.com/MidoriKami/SortaKinda) | 依自訂規則把物品欄與兵裝庫的物品排進指定格子，取代原生 /isort。 |
| [**Peeping Tom**](https://github.com/ffxiv-tc-port/PeepingTom) | [Caraxi](https://github.com/Caraxi/PeepingTom) | 顯示誰正在以你為目標，可保留紀錄、在對方頭上標示，被鎖定時發出提醒。 |
| [**Better Mount Roulette**](https://github.com/ffxiv-tc-port/BetterMountRoulette) | [CMDRNuffin](https://github.com/CMDRNuffin/BetterMountRoulette) | 從自訂坐騎分組隨機召喚。 |
| [**High FPS Physics**](https://github.com/ffxiv-tc-port/xivlauncher_physics_plugin) | [ItsLunaYup](https://github.com/ItsLunaYup/xivlauncher_physics_plugin) | 修正高幀率下布料與頭髮的物理效果異常（把物理效果更新頻率限制在設定值）。 |
| [**ScrollableTabs**](https://github.com/ffxiv-tc-port/ScrollableTabs) | [Haselnussbomber](https://github.com/Haselnussbomber/ScrollableTabs) | 用滑鼠滾輪切換遊戲介面中的分頁（物品欄、兵裝庫、人物、坐騎一覽等）。 |
| [**Brio**](https://github.com/ffxiv-tc-port/Brio) | [Etheirys](https://github.com/Etheirys/Brio) | 集體動作（GPose）的姿勢、動畫、光源、外觀與場景編輯工具。 |
| [**TataruPraise**](https://github.com/ffxiv-tc-port/TataruPraise) | 自製 | 遊戲裡發生事情時用塔塔露的聲線喊一句極短的提示，也讓其他插件透過 IPC 叫它出聲；需要本機 GPT-SoVITS 語音橋接。 |

### 開發工具

| 插件 | 上游 | 說明 |
|------|------|------|
| [**Dynamis**](https://github.com/ffxiv-tc-port/Dynamis) | [Exter-N](https://github.com/Exter-N/Dynamis) | 給開發者的逆向工程與除錯工具箱：物件檢視器、特徵碼掃描、不必外接除錯器的中斷點（IPFD）。 |
| [**Dynamis (with Hosted PowerShell)**](https://github.com/ffxiv-tc-port/Dynamis) | [Exter-N](https://github.com/Exter-N/Dynamis) | Dynamis 含內建 PowerShell 版本 |
| [**Meddle**](https://github.com/ffxiv-tc-port/Meddle) | [PassiveModding](https://github.com/PassiveModding/Meddle) | 把遊戲內角色模型與場景匯出成 glTF，搭配 MeddleTools Blender 附加元件使用（製作／開發工具）。 |

## 共用函式庫

插件本身不是全部——底下這些函式庫也一起維護著台服 fork。它們不會出現在插件清單裡，但**每一個插件都踩在上面**：

### 執行環境與遊戲資料

| 函式庫 | 上游 | 用途 | 誰在用 |
|--------|------|------|--------|
| [**Dalamud**](https://github.com/ffxiv-tc-port/Dalamud) | [yanmucorp](https://github.com/yanmucorp/Dalamud)（原作 [goatcorp](https://github.com/goatcorp/Dalamud)） | 台服專用插件執行環境（API13 / net9），本倉庫所有插件的宿主 | 全部 |
| [**FFXIVClientStructs**](https://github.com/ffxiv-tc-port/FFXIVClientStructs) | [yanmucorp](https://github.com/yanmucorp/FFXIVClientStructs)（原作 [aers](https://github.com/aers/FFXIVClientStructs)） | 遊戲記憶體結構定義——插件靠它讀取遊戲已解析好的狀態 | 全部（隨 Dalamud 出貨） |
| [**Lumina**](https://github.com/ffxiv-tc-port/Lumina) | [NotAdam](https://github.com/NotAdam/Lumina) | 讀取遊戲資料檔（sqpack／EXD 資料表） | 全部（隨 Dalamud 出貨） |

⚠️ `FFXIVClientStructs` 與 `Lumina` 的台服修正**編進 Dalamud 本體一起出貨**，插件端不需要各自引用。

Dalamud 本體另外還有這些原生與綁定相依（只在重建 Dalamud 時用到，插件不直接引用）：

| 函式庫 | 上游 | 用途 | 誰在用 |
|--------|------|------|--------|
| [**FfxivArgLauncher**](https://github.com/ffxiv-tc-port/FfxivArgLauncher) | [ottercorp](https://github.com/ottercorp/FfxivArgLauncher) | 注入器啟動遊戲行程用的輔助函式庫 | `Dalamud` |
| [**minhook**](https://github.com/ffxiv-tc-port/minhook) | [TsudaKageyu](https://github.com/TsudaKageyu/minhook) | Windows x86/x64 極簡 API hook 函式庫 | `Dalamud` |
| [**nmd**](https://github.com/ffxiv-tc-port/nmd) | [Nomade040](https://github.com/Nomade040/nmd) | C/C++ 單一標頭檔函式庫集合（含 x86 組譯／反組譯器） | `Dalamud` |
| [**gc-imgui**](https://github.com/ffxiv-tc-port/gc-imgui) | [goatcorp](https://github.com/goatcorp/gc-imgui) | Dear ImGui 本體——Dalamud 介面繪製的底層 | `gc-cimgui` |
| [**gc-cimgui**](https://github.com/ffxiv-tc-port/gc-cimgui) | [goatcorp](https://github.com/goatcorp/gc-cimgui) | Dear ImGui 的 C API 綁定 | `Dalamud` |
| [**gc-ImGuizmo**](https://github.com/ffxiv-tc-port/gc-ImGuizmo) | [goatcorp](https://github.com/goatcorp/gc-ImGuizmo) | ImGuizmo：Dear ImGui 的 3D 操作軸元件 | `gc-cimguizmo` |
| [**gc-cimguizmo**](https://github.com/ffxiv-tc-port/gc-cimguizmo) | [goatcorp](https://github.com/goatcorp/gc-cimguizmo) | ImGuizmo 的 C API 綁定 | `Dalamud` |
| [**gc-implot**](https://github.com/ffxiv-tc-port/gc-implot) | [goatcorp](https://github.com/goatcorp/gc-implot) | ImPlot：Dear ImGui 的即時繪圖擴充 | `gc-cimplot` |
| [**gc-cimplot**](https://github.com/ffxiv-tc-port/gc-cimplot) | [goatcorp](https://github.com/goatcorp/gc-cimplot) | ImPlot 的 C API 綁定 | `Dalamud` |
| [**Hexa.NET.ImGui**](https://github.com/ffxiv-tc-port/Hexa.NET.ImGui) | [goatcorp](https://github.com/goatcorp/Hexa.NET.ImGui) | Dear ImGui 的 .NET 綁定與產生器（重新產生 ImGui 綁定時用） | `Dalamud` |

### 插件框架與介面

| 函式庫 | 上游 | 用途 | 誰在用 |
|--------|------|------|--------|
| [**ECommons**](https://github.com/ffxiv-tc-port/ECommons) | [NightmareXIV](https://github.com/NightmareXIV/ECommons) | 插件開發框架：服務容器、設定存取、節流、任務佇列、UI 輔助——艦隊裡最廣泛的相依 | 26 個插件 |
| [**ECommons.IPC**](https://github.com/ffxiv-tc-port/ECommons.IPC) | [NightmareXIV](https://github.com/NightmareXIV/ECommons.IPC) | ECommons 的跨插件 IPC 模組（獨立套件） | `AutoDuty`、`AutoRetainer` |
| [**OtterGui**](https://github.com/ffxiv-tc-port/OtterGui) | [Ottermandias](https://github.com/Ottermandias/OtterGui) | ImGui 輔助函式庫：表格、篩選清單、拖放樹狀結構 | `Artisan`、`AutoRetainer`、`GatherBuddyReborn`、`InventoryTools`、`Lifestream` |
| [**PunishLib**](https://github.com/ffxiv-tc-port/PunishLib) | [PunishXIV](https://github.com/PunishXIV/PunishLib) | Puni.sh 系列插件的共用基底（關於視窗、錯誤回報） | 8 個插件 |
| [**KamiToolKit**](https://github.com/ffxiv-tc-port/KamiToolKit) | [MidoriKami](https://github.com/MidoriKami/KamiToolKit) | 原生 UI 節點函式庫——直接繪製遊戲自己的 UI 元件而不是 ImGui | `DailyDuty`、`EzWondrousTails`、`SortaKinda` |
| [**CriticalCommonLib**](https://github.com/ffxiv-tc-port/CriticalCommonLib) | [Critical-Impact](https://github.com/Critical-Impact/CriticalCommonLib) | 物品／容器／僱員資料的快取與查詢層 | `InventoryTools` |
| [**LLib**](https://github.com/ffxiv-tc-port/LLib) | [alydevs](https://github.com/alydevs/LLib) | Dalamud 插件共用輔助（設定視窗、ImGui 小元件） | `Gearsetter` |
| [**KamiLib**](https://github.com/ffxiv-tc-port/KamiLib) | [MidoriKami](https://github.com/MidoriKami/KamiLib) | 角色設定、指令與選擇清單視窗等共用元件 | `DailyDuty`、`Mappy`、`SortaKinda` |
| [**NightmareUI**](https://github.com/ffxiv-tc-port/NightmareUI) | [NightmareXIV](https://github.com/NightmareXIV/NightmareUI) | ImGui 介面元件（含支援台服的世界選單） | `AutoRetainer`、`Lifestream`、`Splatoon`、`TextAdvance` |
| [**NightmareUI.OtterGuiWrapper**](https://github.com/ffxiv-tc-port/NightmareUI.OtterGuiWrapper) | [NightmareXIV](https://github.com/NightmareXIV/NightmareUI.OtterGuiWrapper) | NightmareUI 的 OtterGui 清單轉接層 | `AutoRetainer`、`Lifestream` |
| [**OtterGuiInternal**](https://github.com/ffxiv-tc-port/OtterGuiInternal) | [Ottermandias](https://github.com/Ottermandias/OtterGuiInternal) | OtterGui 呼叫 ImGui 內部 API 的低階輔助 | `OtterGui` |
| [**Ocelot**](https://github.com/ffxiv-tc-port/Ocelot) | [OhKannaDuh](https://github.com/OhKannaDuh/Ocelot) | 模組化插件框架（動作鏈、尋路追蹤、設定畫面） | `BOCCHI` |
| [**ECommons.FileWriter**](https://github.com/ffxiv-tc-port/ECommons.FileWriter) | [Limiana](https://github.com/Limiana/ECommons.FileWriter) | ECommons 的獨立行程檔案寫入程式 | `AutoRetainer` |

### 繪製、導航與遊戲邏輯

| 函式庫 | 上游 | 用途 | 誰在用 |
|--------|------|------|--------|
| [**Pictomancy**](https://github.com/ffxiv-tc-port/Pictomancy) | [sourpuh](https://github.com/sourpuh/ffxiv_pictomancy)（另併入 [Limiana](https://github.com/Limiana/ffxiv_pictomancy) 的分支） | 繪製 3D 世界疊加層與 VFX | `AutoDuty`、`ExplorersIcebox`、`ICE`、`Questionable`、`Ocelot`（`BOCCHI`）、`Splatoon` |
| [**DotRecast**](https://github.com/ffxiv-tc-port/DotRecast) | [xanunderscore](https://github.com/xanunderscore/DotRecast) | Recast & Detour 導航網格的 C# 移植 | `vnavmesh` |
| [**machina**](https://github.com/ffxiv-tc-port/machina) | [PlusoneChiang](https://github.com/PlusoneChiang/machina) | 讀取網路資料並解出遊戲訊息的函式庫，含台服 opcode 表 | `IINACT` |
| [**FFTriadBuddy**](https://github.com/ffxiv-tc-port/FFTriadBuddy) | [MgAl2O4](https://github.com/MgAl2O4/FFTriadBuddy) | 九宮幻卡遊戲邏輯與解牌器 | `TriadBuddy` |

### 跨插件 IPC 介面

這幾個是「介面套件」——讓別的插件能安全呼叫某個插件的功能，而不必直接相依它的實作：

| 函式庫 | 上游 | 用途 | 誰在用 |
|--------|------|------|--------|
| [**AutoRetainerAPI**](https://github.com/ffxiv-tc-port/AutoRetainerAPI) | [PunishXIV](https://github.com/PunishXIV/AutoRetainerAPI) | 呼叫 `AutoRetainer` 的僱員／潛水艇排程 | `AutoRetainer`、`GatherBuddyReborn`、`Lifestream`、`SomethingNeedDoing`、`visland` |
| [**WrathCombo.API**](https://github.com/ffxiv-tc-port/WrathCombo.API) | [PunishXIV](https://github.com/PunishXIV/WrathCombo.API) | 向 `WrathCombo` 借用戰鬥連段判斷 | `AutoDuty` |
| [**NotificationMasterAPI**](https://github.com/ffxiv-tc-port/NotificationMasterAPI) | [NightmareXIV](https://github.com/NightmareXIV/NotificationMasterAPI) | 送出桌面／手機通知 | `NotificationMaster`（其餘 5 個插件吃上游公開 NuGet 版） |

### 資料與求解工具

| 專案 | 上游 | 用途 |
|------|------|------|
| [**raphael-rs**](https://github.com/ffxiv-tc-port/raphael-rs) | [KonaeAkira](https://github.com/KonaeAkira/raphael-rs) | 製作流程求解器（Rust），`Artisan` 的宇宙探索專家配方解算用 |
| [**xiv-data-oxidizer**](https://github.com/ffxiv-tc-port/xiv-data-oxidizer) | [cycleapple](https://github.com/cycleapple/xiv-data-oxidizer)（原作 [fae-industries](https://github.com/fae-industries/xiv-data-oxidizer)） | 把台服遊戲資料匯出成 CSV，移植時比對台服與國際服差異的依據 |
| [**ff14-submarine**](https://github.com/ffxiv-tc-port/ff14-submarine) | [yueecui](https://github.com/yueecui/ff14-submarine) | 潛水艇航線與配裝規劃網頁工具（台服 7.20 資料、繁體中文化） |
| [**EXDSchema**](https://github.com/ffxiv-tc-port/EXDSchema) | [xivdev](https://github.com/xivdev/EXDSchema) | 遊戲 Excel（EXD）資料表的欄位定義，`xiv-data-oxidizer` 匯出台服 EXD 時用 |
| [**XIVTCLauncher**](https://github.com/ffxiv-tc-port/XIVTCLauncher) | [cycleapple](https://github.com/cycleapple/XIVTCLauncher) | 台服啟動器 FFXIVSimpleLauncher 的原始碼鏡像（僅供查閱） |

> 🔑 **為什麼函式庫也要 fork**：台服的記憶體結構位移、資料表欄位、語言判斷都與國際服不同，而這些差異多半落在函式庫這一層。上游多數已經跟進 API15／net10，我們釘在 API13，因此走各自的分支。

## 為什麼需要這個倉庫？

台服使用的 Dalamud 版本與官方插件倉庫所要求的 API 等級不一致，導致許多實用插件無法直接安裝。本倉庫維護這些插件對應 API13 的相容版本，並修正因客戶端差異導致的問題——包括記憶體結構位移、語言判斷、台服資料表差異（NPC／道具 ID 不同、未開放內容）等。

## 繁體中文化

所有收錄插件的介面均已翻譯為繁體中文：

- **翻譯外部化**：程式碼保留英文原文作為鍵值，譯文存放於外部語言檔（ini／resx／json），跟隨上游更新時翻譯不會遺失
- 修正了多個台服客戶端語言判斷問題（部分插件先前因此永遠顯示英文）

## 貢獻 / 回報問題

插件在台服上的相容性問題，請至上表對應的 **ffxiv-tc-port fork** 開 issue（上游專案不處理台服相容性）。翻譯用語建議也歡迎回報。
