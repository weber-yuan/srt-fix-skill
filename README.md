# srt-fix — 字幕修正與斷句工具

一個給 **Claude Code / Claude Cowork** 用的工具（透過 `CLAUDE.md` 自動載入）：把現成的 SRT 字幕（通常來自語音辨識 / ASR）整理乾淨——**修正同音字與錯別字、正規化數字格式、再把長句斷成適合上字幕的短句**。輸出繁體中文（台灣用語）。

## 它做什麼

| 能力 | 說明 |
|------|------|
| 數字格式規範化 | 百分之百→100%、百分之十→10%、兩百→200、兩千→2000（自動避開「一兩百」等量詞） |
| ASR 同音字 / 錯別字修正 | 由 Claude 結合上下文找出，例：神經網落→神經網路、西洋騎→西洋棋、Cloud→Claude |
| 斷句 | 長句斷成 ≤N 字短句，標點優先，時間戳依字數比例自動重算 |
| 先確認再套用 | 預設會把修改清單列給你過目、同意後才寫檔 |

**不會做**：增刪實詞、同義詞替換、刪口語重複、亂動時間戳——寧可漏改不誤改，保住真實語音。

## 設計

機械性、確定性的工作（解析 SRT、套規則、驗證、全文取代、斷句時間戳插值、大檔 I/O）交給隨附的 Python 腳本 `scripts/srt_tools.py`（**純標準庫，零 pip 安裝**）；需要語意判斷的「哪個字是同音錯字」由執行 skill 的 Claude 親自做。

## 流程

整條是一條**序列管線**：每一步吃前一步的產物，必須依序執行。只有「找錯字」那一步是 AI 做的，其餘都是 Python 腳本的機械處理。

| 步驟 | 由誰執行 | 讀入 | 產出 |
|------|----------|------|------|
| 1. `prepare` | 🐍 Python | 原始 `.srt` | `<名>.work.json`、`<名>.prepared.txt` |
| 2. 找同音字/錯別字 | 🤖 AI（Claude） | `<名>.prepared.txt` | `<名>.corrections.json` |
| 3. 給你確認 | 🤖 AI ＋ 你 | `<名>.corrections.json` | 核准/挑選後的清單 |
| 4. `apply` | 🐍 Python | `.work.json` ＋ `.corrections.json` | `<名>.corrected.srt` |
| 5. `split` | 🐍 Python | `<名>.corrected.srt` | `<名>.final.srt` |

- **能不能並行？** 單一檔案內**不行**——每步都依賴上一步的輸出（例如 `split` 一定要先有 `corrected.srt`）。若一次處理**多個**字幕檔，各檔的管線彼此獨立，可分開並行。
- **步驟 2 遇到沒把握的專有名詞**（人名/機構/學校/品牌）時，AI 會先**上網查證**正確寫法再決定要不要改，而不是憑空猜。

**最終輸出**：`<名>.corrected.srt`（修正後）、`<名>.final.srt`（斷句後），外加一份修改摘要。

## 安裝與使用

採用 **`CLAUDE.md` 自動載入**機制：Claude Code / Cowork 會**自動讀取工作目錄下的 `CLAUDE.md`**。所以你只要把檔案放進一個工作目錄，就能直接用——**不需要 `.claude/` 隱藏目錄、也不需要任何「skill 安裝」步驟**（這正好避開 Cowork sandbox 不能建立 `.` 開頭目錄的限制）。

1. 指定一個工作目錄（建議用一個**專門處理字幕的資料夾**，例如 `my-subs/`）。
2. 把 repo 的檔案**直接放進該目錄**——**不要再多包一層 `srt-fix/`**。放好後，`CLAUDE.md`、`scripts/`、`data/` 應該就直接在工作目錄底下：

   ```
   my-subs/
   ├── CLAUDE.md
   ├── scripts/srt_tools.py
   ├── data/vocab.json
   └── examples/ …
   ```

   兩種做法：
   - **下載 ZIP（最簡單）**：GitHub → **Code → Download ZIP**，解壓後把**裡面的檔案**（不是外層那層資料夾）搬進工作目錄。
   - **clone 到目前目錄**（目錄需為空，注意結尾的 `.`）：

     ```bash
     cd my-subs
     git clone https://github.com/weber-yuan/srt-fix-skill .
     ```

3. **使用方式**：把你的 `.srt` 也放進這個目錄，然後直接說：

   > 「**幫我修正字幕 `xxx.srt`**」

   `CLAUDE.md` 已被自動讀取，Claude 會照裡面的流程去跑 `scripts/srt_tools.py`，不必你特別點名。

> ⚠️ 如果這個目錄**原本就有自己的 `CLAUDE.md`**，直接覆蓋會蓋掉你原有的專案指示。這時請改用一個獨立資料夾，或把本檔內容**附加**到你現有的 `CLAUDE.md` 後面。

### 需要安裝什麼？

只需要 **Python 3.10 以上**，**除此之外什麼都不用裝**——腳本只用 Python 標準庫，不需要 `pip install` 任何套件；找錯字用的上網查證是 Claude 內建功能。

- 在 **Claude Code / Cowork** 裡通常**不必另外安裝**：執行環境本來就內建 Python（Anthropic 官方 skills 如 pdf/docx/xlsx 也都靠 Python 跑）。
- 在**自己的機器**上：skill 會在開始前先執行 `python --version` 確認；若沒裝或版本過舊，會**先詢問你**是否要協助安裝，同意後再依作業系統安裝：
  - Windows：`winget install Python.Python.3.12`（或 <https://www.python.org/downloads/>）
  - macOS：`brew install python`
  - Linux：`sudo apt install -y python3`

## 直接用腳本（不透過 Claude 也行）

```bash
python scripts/srt_tools.py prepare 你的字幕.srt --vocab data/vocab.json --seeds 來賓名 品牌名
# 自己編輯 你的字幕.corrections.json（[{"original","corrected","reason"}, ...]）
python scripts/srt_tools.py apply 你的字幕.work.json 你的字幕.corrections.json
python scripts/srt_tools.py split 你的字幕.corrected.srt --max-chars 20
```

## 範例

`examples/` 內含一個刻意放了錯字的 `sample.qwen.srt`，以及修正清單與前後輸出，可照流程跑一遍體驗。

## 候選詞庫（可選）

`data/vocab.json` 可填入你領域裡容易被聽錯的人名/品牌/術語，prepare 掃描時會提示 Claude 留意。預設只有一個範例條目，可自由擴充。

## 授權與出處

本 skill 衍生自 `sunyuzheng/kdb-post-production`。請依原專案授權條款使用；本 repo 的改作部分以 MIT 釋出（見 `LICENSE`）。
