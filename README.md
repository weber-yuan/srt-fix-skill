# srt-fix — 字幕修正與斷句 Skill

一個給 **Claude Code / Claude Cowork** 用的 skill：把現成的 SRT 字幕（通常來自語音辨識 / ASR）整理乾淨——**修正同音字與錯別字、正規化數字格式、再把長句斷成適合上字幕的短句**。輸出繁體中文（台灣用語）。

> 衍生並重構自 [`sunyuzheng/kdb-post-production`](https://github.com/sunyuzheng/kdb-post-production) 的字幕校對與斷句邏輯。把六步流水線中的「字幕修正＋斷句」單獨抽出，做成跨平台、agent 驅動、零外部依賴的獨立 skill，並修正了原專案在 Windows 與驗證層上的數個問題。

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

```
輸入：任意 .srt（+ 可選本集專有名詞、可選自訂 vocab.json）
  │
 [1] srt_tools.py prepare  → 套數字規則 + 掃描候選詞 → 產出精簡逐字稿 + 工作檔
 [2] Claude 讀逐字稿 → 列出修正清單 {original, corrected, reason}
 [3] 先把清單給你確認（可挑掉某幾筆）
 [4] srt_tools.py apply    → 驗證守門 + 全文取代 → <名>.corrected.srt
 [5] srt_tools.py split    → 斷句 → <名>.final.srt
  │
輸出：<名>.corrected.srt、<名>.final.srt ＋ 修改摘要
```

## 安裝

把整個資料夾放到 Claude 的 skills 目錄即可（資料夾名就是 skill 名 `srt-fix`）：

- **只在某個專案用**：放到該專案的 `.claude/skills/srt-fix/`
- **全域（含各處 Cowork）用**：放到 `~/.claude/skills/srt-fix/`

```bash
git clone https://github.com/<你的帳號>/srt-fix-skill ~/.claude/skills/srt-fix
```

之後在 Claude Code / Cowork 裡，丟一個 `.srt` 給它、說「幫我修正這個字幕」即可，skill 會自動被喚起。

### 需要 Python 嗎？

需要，但你通常**不必另外安裝**：Claude Code 與 Cowork 的執行環境本來就內建 Python（Anthropic 官方 skills 如 pdf/docx/xlsx 也都靠 Python 跑）。腳本只用標準庫，不需任何 pip 套件。

## 直接用腳本（不透過 Claude 也行）

```bash
python scripts/srt_tools.py prepare 你的字幕.srt --vocab data/vocab.json --seeds 嘉賓名 品牌名
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
