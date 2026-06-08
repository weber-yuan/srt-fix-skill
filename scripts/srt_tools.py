#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
srt_tools.py —— 字幕修正/斷句的「機械層」工具（不含 LLM）

這支腳本只做「確定性、機械性」的工作；真正需要語意判斷的「找同音字/錯別字」
交給執行本 skill 的 Claude 自己做。分工如下：

    prepare   解析 SRT → 套數字格式規則 → 掃描候選詞 → 產出給 Claude 看的精簡逐字稿
    apply     拿 Claude 給的修正清單 → 驗證(守門) → 全文取代 → 寫出 .corrected.srt
    split     把長句斷成適合上字幕的短句，時間戳按字數比例插值 → 寫出 .final.srt

設計重點：
  - 只用 Python 標準庫，零 pip 安裝。
  - 一律以 UTF-8 讀寫檔案；並把 stdout/stderr 切到 UTF-8，避免 Windows
    主控台預設 cp950 遇到中文/特殊字元（✓ → ⚠）時崩潰。
  - 「修正」會把同一個錯字在全文「所有出現處」一起改（不是只改第一處）。

"""

import argparse
import json
import re
import sys
from pathlib import Path

# ── Windows 主控台編碼保護 ───────────────────────────────────────────────────
# Windows 預設用 cp950/locale 編碼輸出，遇到繁體字或 ✓ → ⚠ 等字元會直接拋例外。
# 把 stdout/stderr 重設為 UTF-8，讓本腳本在任何平台都能正常列印訊息。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # 某些被重導向的串流沒有 reconfigure，忽略即可。
        pass


# ════════════════════════════════════════════════════════════════════════════
#  一、數字格式規範化規則（純規則，不走 LLM）
# ════════════════════════════════════════════════════════════════════════════
# 把口語/中文數字寫法，正規化成適合字幕顯示的阿拉伯數字或百分比。
# 帶 boundary_guard 的規則，會避免誤改「一兩百」「三兩千」這類前面跟著數量詞的情況。
#
# 注意：原專案曾有一條「到十 → 10」規則，但它會把「撐到十二年 / 六到十根 / 到十五年」
# 中的「到十」誤改成「10」，屬於有害規則，已永久移除。
_FORMAT_RULES: list[dict] = [
    {"pat": "百分之百", "rep": "100%"},
    {"pat": "百分之十", "rep": "10%"},
    {"pat": "兩百",     "rep": "200",  "boundary_guard": True},   # 一兩百 不改
    {"pat": "兩千",     "rep": "2000", "boundary_guard": True},   # 一兩千 不改
    {"pat": "么么",     "rep": "11"},                             # 方言把 11 念成「么么」
]
# 出現在這些字之後的「兩百/兩千」屬於量詞用法（如「一兩百」），不做替換。
_BOUNDARY_PRECEDING = set("一二三四五六七八九十")
_FORMAT_PAT_SET = {r["pat"] for r in _FORMAT_RULES}


# ════════════════════════════════════════════════════════════════════════════
#  二、SRT 解析 / 輸出
# ════════════════════════════════════════════════════════════════════════════

def parse_srt(path: Path) -> list[dict]:
    """讀取 SRT，回傳 [{'timestamp': '...', 'text': '...'}, ...]。

    以「空白行」切分區塊；每個區塊取出時間戳那一行與文字內容（略過純數字的序號行）。
    用 errors='replace' 容忍少數壞位元組，不讓整份檔案因一個壞字元而讀不進來。
    """
    content = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n{2,}", content.strip())
    chunks: list[dict] = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        ts_line = next((l for l in lines if "-->" in l), "")
        text_lines = [l for l in lines if not l.strip().isdigit() and "-->" not in l]
        text = "\n".join(text_lines).strip()
        if not text:
            continue
        chunks.append({"timestamp": ts_line, "text": text})
    return chunks


def write_srt(chunks: list[dict], path: Path) -> None:
    """把 chunks 重新編號後寫成標準 SRT（UTF-8）。"""
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(chunks, 1):
            f.write(f"{i}\n{c['timestamp']}\n{c['text']}\n\n")


# ════════════════════════════════════════════════════════════════════════════
#  三、規則層：數字格式規範化
# ════════════════════════════════════════════════════════════════════════════

def apply_format_rules(chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    """套用數字格式規則，回傳 (新的 chunks, 變更清單)。

    變更清單每筆為 {'before': 原句, 'after': 改後, 'rule': 'pat→rep'}，供摘要顯示。
    """
    result = [dict(c) for c in chunks]
    changes: list[dict] = []
    for rule in _FORMAT_RULES:
        pat, rep = rule["pat"], rule["rep"]
        boundary = rule.get("boundary_guard", False)
        for chunk in result:
            text = chunk["text"]
            if pat not in text:
                continue
            if boundary:
                # 逐次掃描，跳過前面是數量字（一二三…十）的情況，避免改壞「一兩百」。
                new_text = ""
                i = 0
                changed = False
                while i < len(text):
                    pos = text.find(pat, i)
                    if pos == -1:
                        new_text += text[i:]
                        break
                    if pos > 0 and text[pos - 1] in _BOUNDARY_PRECEDING:
                        new_text += text[i:pos + len(pat)]   # 保留原文
                    else:
                        new_text += text[i:pos] + rep
                        changed = True
                    i = pos + len(pat)
                if changed:
                    changes.append({"before": text, "after": new_text, "rule": f"{pat}→{rep}"})
                    chunk["text"] = new_text
            else:
                if pat in text:
                    new_text = text.replace(pat, rep)
                    if new_text != text:
                        changes.append({"before": text, "after": new_text, "rule": f"{pat}→{rep}"})
                        chunk["text"] = new_text
    return result, changes


# ════════════════════════════════════════════════════════════════════════════
#  四、候選詞庫：產生「可疑點提示」給 Claude 參考
# ════════════════════════════════════════════════════════════════════════════

def load_vocab(vocab_path: Path | None) -> dict:
    """載入候選詞庫（JSON）。檔案不存在或未指定時回傳空庫。"""
    if vocab_path and vocab_path.exists():
        try:
            return json.loads(vocab_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def scan_hints(chunks: list[dict], vocab: dict, seeds: list[str]) -> list[dict]:
    """掃描全文，找出候選詞庫裡列出的「容易聽錯」詞彙的出現位置，做成提示清單。

    這些只是「提示」，不是強制修改——最終是否要改由 Claude 結合上下文判斷。
    seeds（本集來賓名/專有名詞）也會被當成重要詞，提醒 Claude 留意一致性。
    """
    candidates = dict(vocab.get("verified_candidates", {}))
    hints: list[dict] = []
    seen: set = set()
    for pat in sorted(candidates.keys(), key=len, reverse=True):
        info = candidates[pat]
        for ci, chunk in enumerate(chunks):
            text = chunk["text"]
            pos = text.find(pat)
            if pos == -1:
                continue
            key = (pat,)  # 同一個候選詞只提示一次即可
            if key in seen:
                continue
            seen.add(key)
            ctx = text[max(0, pos - 12): pos + len(pat) + 12]
            hints.append({
                "found": pat,
                "alternatives": info.get("alternatives", []),
                "hint": info.get("hint", ""),
                "context": ctx,
            })
            break
    for seed in seeds:
        seed = seed.strip()
        if seed and any(seed in c["text"] for c in chunks):
            hints.append({"found": seed, "alternatives": [], "hint": "本集指定專有名詞，請確認全文寫法一致", "context": ""})
    return hints


# ════════════════════════════════════════════════════════════════════════════
#  五、修正清單的驗證（守門）與套用
# ════════════════════════════════════════════════════════════════════════════

def _edit_distance_approx(a: str, b: str) -> int:
    """近似編輯距離：位置對齊後不同字數 + 長度差。用來擋住「大幅改寫」型的修正。"""
    if a == b:
        return 0
    common = sum(x == y for x, y in zip(a, b))
    return (len(a) - common) + (len(b) - common)


_DIGIT_CHARS = set("0123456789零一二三四五六七八九十百千萬億兩")


def _has_digit(s: str) -> bool:
    return any(c in _DIGIT_CHARS for c in s)


def validate_corrections(corrections: list[dict], full_text: str) -> tuple[list[dict], list[dict]]:
    """驗證 Claude 給的修正清單，回傳 (通過的, 被擋下的)。

    守門規則的用意：寧可漏改，不要誤改。逐條檢查——
      - original/corrected 必須有值且不相等
      - original 必須真的出現在字幕全文裡（否則無從取代）
      - 不改純數字
      - 中文片段最長 6 字；含英文的術語放寬到 12 字（如 Superlinear）
      - 改後長度不能比原文長太多（>2 視為擴寫，擋）
      - 純中文修正的編輯距離 > 4 視為改寫，擋
      - 去重（同一個 original 只保留第一筆）
    """
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen: set = set()
    for item in corrections:
        orig = (item.get("original") or "").strip()
        corr = (item.get("corrected") or "").strip()
        reason = item.get("reason", "")

        def reject(why: str):
            rejected.append({"original": orig, "corrected": corr, "reason": reason, "rejected_because": why})

        if not orig or not corr or orig == corr:
            reject("原文或修正為空、或兩者相同")
            continue
        if orig not in full_text:
            reject("原文未出現在字幕中")
            continue
        if orig in seen:
            reject("重複（同一原文已收錄）")
            continue
        has_en = bool(re.search(r"[A-Za-z]", orig))
        if orig.isdigit() or corr.isdigit() or (_has_digit(orig) and _has_digit(corr) and not has_en):
            reject("涉及純數字，不改")
            continue
        if len(orig) > 6 and not has_en:
            reject("中文片段過長（>6 字）")
            continue
        if len(orig) > 12:
            reject("片段過長（>12 字）")
            continue
        if len(corr) - len(orig) > 2:
            reject("改後明顯變長，疑似擴寫")
            continue
        if not has_en and _edit_distance_approx(orig, corr) > 4:
            reject("修改幅度過大，疑似改寫")
            continue
        seen.add(orig)
        accepted.append({"original": orig, "corrected": corr, "reason": reason})
    return accepted, rejected


def apply_corrections(chunks: list[dict], corrections: list[dict]) -> tuple[list[dict], dict]:
    """把通過驗證的修正套到全文「所有出現處」，回傳 (新 chunks, 每筆取代次數)。"""
    result = [dict(c) for c in chunks]
    counts: dict = {}
    for corr in corrections:
        o, r = corr["original"], corr["corrected"]
        n = 0
        for chunk in result:
            if o in chunk["text"]:
                n += chunk["text"].count(o)
                chunk["text"] = chunk["text"].replace(o, r)
        counts[f"{o}→{r}"] = n
    return result, counts


# ════════════════════════════════════════════════════════════════════════════
#  六、斷句：把長句斷成 ≤N 字的短句，時間戳按字數比例插值
# ════════════════════════════════════════════════════════════════════════════

DEFAULT_MAX_CHARS = 20
_TS_RE = re.compile(r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)")


def _parse_ts(ts_line: str) -> tuple[float, float]:
    """把『HH:MM:SS,mmm --> HH:MM:SS,mmm』解析成 (起秒, 訖秒)。"""
    m = _TS_RE.search(ts_line)
    if not m:
        return 0.0, 0.0
    h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x) for x in m.groups())
    return (h1 * 3600 + m1 * 60 + s1 + ms1 / 1000,
            h2 * 3600 + m2 * 60 + s2 + ms2 / 1000)


def _fmt_ts(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# 把文字切成「原子」：英數字/百分比的連續片段視為一個不可分割的原子（如 100%、Python、
# 2050），其餘每個中文字各自成一個原子。這樣斷句時就絕不會把 100% 切成 1 / 00%。
_ATOM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9%.\-]*|\s+|.")


def _pack_atoms(piece: str, max_chars: int) -> list[str]:
    """把一段沒有標點可切的文字，以『原子』為單位貪婪打包成每段 ≤ max_chars。

    保證不切進英數字 token；只有當單一原子本身就超長（罕見，如超長網址）才強制硬切。
    """
    atoms = _ATOM_RE.findall(piece)
    segments: list[str] = []
    buf = ""
    for atom in atoms:
        if atom.isspace():
            # 空白：能併就併（保留中英之間的空格），不單獨成段。
            if buf and len(buf) + len(atom) <= max_chars:
                buf += atom
            continue
        if len(buf) + len(atom) <= max_chars:
            buf += atom
        else:
            if buf.strip():
                segments.append(buf.strip())
            if len(atom) > max_chars:           # 單一原子超長 → 不得已硬切
                while len(atom) > max_chars:
                    segments.append(atom[:max_chars])
                    atom = atom[max_chars:]
            buf = atom
    if buf.strip():
        segments.append(buf.strip())
    return segments


def split_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """把一段文字切成每段 ≤ max_chars 字，盡量在標點處切以保語意完整。

    斷句優先序：句末標點（。！？）> 子句標點（，；、：）> 原子打包（不切進英數字 token）。
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    segments: list[str] = []
    # 第一刀：句末標點之後
    for part in (p.strip() for p in re.split(r"(?<=[。！？])", text) if p.strip()):
        if len(part) <= max_chars:
            segments.append(part)
            continue
        # 第二刀：子句標點之後，邊切邊貪婪併段
        buf = ""
        for cp in (p.strip() for p in re.split(r"(?<=[，；、：])", part) if p.strip()):
            if len(buf) + len(cp) <= max_chars:
                buf += cp
            else:
                if buf:
                    segments.append(buf)
                    buf = ""
                if len(cp) > max_chars:
                    # 第三刀：原子打包（保住 100%、Python 等不被切斷）
                    packed = _pack_atoms(cp, max_chars)
                    segments.extend(packed[:-1])
                    buf = packed[-1] if packed else ""
                else:
                    buf = cp
        if buf:
            segments.append(buf)
    return segments or [text]


def resplit(chunks: list[dict], max_chars: int = DEFAULT_MAX_CHARS) -> list[dict]:
    """對每個字幕區塊斷句；若一句被切成多段，依字數比例把原時間區間分配給各段。"""
    out: list[dict] = []
    for chunk in chunks:
        t_start, t_end = _parse_ts(chunk["timestamp"])
        # 斷句前先把內部換行併為空白，避免把顯示換行誤當斷點。
        segments = split_text(chunk["text"].replace("\n", " "), max_chars)
        if len(segments) <= 1:
            out.append(chunk)
            continue
        total = sum(len(s) for s in segments) or 1
        duration = max(t_end - t_start, 0.0)
        cursor = t_start
        for seg in segments:
            seg_end = cursor + duration * (len(seg) / total)
            out.append({"timestamp": f"{_fmt_ts(cursor)} --> {_fmt_ts(seg_end)}", "text": seg})
            cursor = seg_end
    return out


# ════════════════════════════════════════════════════════════════════════════
#  七、輔助：去掉副檔名後綴，取得乾淨的檔名 stem
# ════════════════════════════════════════════════════════════════════════════

def clean_stem(path: Path) -> str:
    """把 a.qwen.srt / a.corrected.srt / a.srt 都還原成 'a'。"""
    name = path.name
    for suf in (".qwen.srt", ".corrected.srt", ".final.srt", ".srt"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return path.stem


# ════════════════════════════════════════════════════════════════════════════
#  八、子命令實作
# ════════════════════════════════════════════════════════════════════════════

def cmd_prepare(args) -> None:
    """prepare：解析 + 套數字規則 + 掃描候選詞 → 寫工作檔與精簡逐字稿。"""
    src = Path(args.input).resolve()
    if not src.exists():
        print(f"錯誤：找不到輸入檔 {src}")
        sys.exit(1)

    chunks = parse_srt(src)
    if not chunks:
        print(f"錯誤：SRT 解析後無內容 {src.name}")
        sys.exit(1)

    chunks, fmt_changes = apply_format_rules(chunks)
    vocab = load_vocab(Path(args.vocab).resolve() if args.vocab else None)
    seeds = args.seeds or []
    hints = scan_hints(chunks, vocab, seeds)

    stem = clean_stem(src)
    out_dir = src.parent
    work_path = out_dir / f"{stem}.work.json"
    prepared_path = out_dir / f"{stem}.prepared.txt"

    # 工作檔：保存套完數字規則後的 chunks 狀態，供 apply 使用。
    work_path.write_text(json.dumps({
        "input": str(src),
        "stem": stem,
        "chunks": chunks,
        "fmt_changes": fmt_changes,
        "hints": hints,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 精簡逐字稿：給 Claude 讀的純文字（每行一個區塊，含索引方便對照）。
    lines = [f"[{i}] {c['text']}" for i, c in enumerate(chunks)]
    prepared_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"✓ prepare 完成：{len(chunks)} 個區塊")
    print(f"  數字格式規範化：{len(fmt_changes)} 處")
    print(f"  候選詞提示：{len(hints)} 則")
    print(f"  逐字稿（給 Claude 閱讀）：{prepared_path}")
    print(f"  工作檔（供 apply 使用）：{work_path}")
    if fmt_changes:
        print("  ── 數字格式變更 ──")
        for ch in fmt_changes[:50]:
            print(f"    [{ch['rule']}] {ch['before']}  →  {ch['after']}")
    if hints:
        print("  ── 候選詞提示（供參考，是否改由 Claude 判斷）──")
        for h in hints[:80]:
            alt = "、".join(h["alternatives"]) if h["alternatives"] else "?"
            print(f"    「{h['found']}」→「{alt}」 {h['hint']} 上下文:…{h['context']}…")


def cmd_apply(args) -> None:
    """apply：載入工作檔 + Claude 的修正清單 → 驗證 → 全文取代 → 寫 .corrected.srt。"""
    work_path = Path(args.work).resolve()
    corr_path = Path(args.corrections).resolve()
    if not work_path.exists():
        print(f"錯誤：找不到工作檔 {work_path}（請先執行 prepare）")
        sys.exit(1)
    if not corr_path.exists():
        print(f"錯誤：找不到修正清單 {corr_path}")
        sys.exit(1)

    work = json.loads(work_path.read_text(encoding="utf-8"))
    chunks = work["chunks"]
    corrections = json.loads(corr_path.read_text(encoding="utf-8"))
    if not isinstance(corrections, list):
        print("錯誤：修正清單必須是 JSON 陣列，每筆為 {original, corrected, reason}")
        sys.exit(1)

    full_text = "\n".join(c["text"] for c in chunks)
    accepted, rejected = validate_corrections(corrections, full_text)
    corrected_chunks, counts = apply_corrections(chunks, accepted)

    stem = work.get("stem") or clean_stem(Path(work["input"]))
    out_path = Path(work["input"]).parent / f"{stem}.corrected.srt"
    write_srt(corrected_chunks, out_path)

    total = sum(counts.values())
    print(f"✓ apply 完成：套用 {len(accepted)} 種修正、共 {total} 處；擋下 {len(rejected)} 筆")
    if accepted:
        print("  ── 已套用 ──")
        for k, v in counts.items():
            print(f"    {k}（{v} 處）")
    if rejected:
        print("  ── 被守門擋下（未套用）──")
        for r in rejected:
            print(f"    {r['original']}→{r['corrected']}：{r['rejected_because']}")
    print(f"  輸出：{out_path}")


def cmd_split(args) -> None:
    """split：把字幕斷成 ≤max-chars 字的短句 → 寫 .final.srt。"""
    src = Path(args.input).resolve()
    if not src.exists():
        print(f"錯誤：找不到輸入檔 {src}")
        sys.exit(1)
    chunks = parse_srt(src)
    out_chunks = resplit(chunks, max_chars=args.max_chars)
    stem = clean_stem(src)
    out_path = Path(args.output).resolve() if args.output else src.parent / f"{stem}.final.srt"
    write_srt(out_chunks, out_path)
    print(f"✓ split 完成：{len(chunks)} → {len(out_chunks)} 條（每條 ≤{args.max_chars} 字）")
    print(f"  輸出：{out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="字幕修正/斷句的機械層工具（不含 LLM）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare", help="解析 SRT + 套數字規則 + 掃描候選詞")
    p_prepare.add_argument("input", help="輸入 .srt 路徑")
    p_prepare.add_argument("--vocab", default=None, help="候選詞庫 JSON 路徑（可選）")
    p_prepare.add_argument("--seeds", nargs="*", default=[], help="本集專有名詞/來賓名（可選）")
    p_prepare.set_defaults(func=cmd_prepare)

    p_apply = sub.add_parser("apply", help="套用 Claude 的修正清單")
    p_apply.add_argument("work", help="prepare 產出的 .work.json")
    p_apply.add_argument("corrections", help="修正清單 JSON（陣列：{original,corrected,reason}）")
    p_apply.set_defaults(func=cmd_apply)

    p_split = sub.add_parser("split", help="斷句")
    p_split.add_argument("input", help="輸入 .srt 路徑（通常是 .corrected.srt）")
    p_split.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=f"每條最大字數（預設 {DEFAULT_MAX_CHARS}）")
    p_split.add_argument("-o", "--output", default=None, help="輸出路徑（預設 <stem>.final.srt）")
    p_split.set_defaults(func=cmd_split)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
