# -*- coding: utf-8 -*-
"""
CET4 题库提取器（Flet 桌面小工具）
=================================

把四六级真题纯文本解析成 assets/data/articles.json 所需的结构，并追加到题库
数组末尾。界面极简：粘贴文本 → 提取并追加 → 看状态。

文本格式（推荐）
----------------
【文章信息】
年份：2025
月份：6
套数：第一套
题型：仔细阅读 Passage 1
文章ID：2025_06_set1_p1

【文章】
第一段正文……

第二段正文……

【段落翻译】
第一段中文翻译……

第二段中文翻译……

【题目】
1. What is the main idea?
A. Option A
B. Option B
C. Option C
D. Option D
答案：B
解析：……
题干翻译：……
选项翻译：
A. ……
B. ……
C. ……
D. ……

2. ……

说明：
- “文章信息”各字段可省略；年份/月份/套数/题型会尽量从标题自动识别。
- 文章ID可省略，程序会根据年份、月份、套数、题型自动生成并去重。
- 每道题必须有 A-D 四个选项和“答案：A/B/C/D”。
- 解析、题干翻译、选项翻译可以省略，缺失时写入空字符串。
- 文章正文用空行分段，段落翻译用空行对应分段。
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import flet as ft

APP_TITLE = "CET4 题库提取器"
SOURCE_DIR = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "_MEIPASS", None) else SOURCE_DIR
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else SOURCE_DIR

OPTION_LETTERS = ("A", "B", "C", "D")
TYPE_KEYWORDS = ("仔细阅读", "长篇阅读", "选词填空", "快速阅读", "信息匹配")
CN_NUMBERS = {
    1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
    6: "六", 7: "七", 8: "八", 9: "九", 10: "十",
}

QUESTION_RE = re.compile(
    r"^\s*(?:Question\s*)?([1-9]\d{0,2})\s*[.、．)]\s*(.*)$",
    re.IGNORECASE,
)
OPTION_RE = re.compile(r"^\s*([A-D])\s*[.、．:：)]\s*(.*)$", re.IGNORECASE)
QUESTION_MARKER_RE = re.compile(
    r"^\s*【?\s*(?:题目|Questions?|问题)\s*】?\s*[:：]?\s*$",
    re.IGNORECASE,
)
SECTION_LABEL_RE = re.compile(
    r"^\s*【?\s*(?:文章|文章信息|Passage|Reading\s+Passage)\s*】?\s*[:：]?\s*$",
    re.IGNORECASE,
)
TRANSLATION_LABEL_RE = re.compile(
    r"^\s*【?\s*(?:段落翻译|全文翻译|参考译文|译文|paragraph\s+translations?)"
    r"\s*】?\s*[:：]?\s*(.*)$",
    re.IGNORECASE,
)
DIRECTION_RE = re.compile(
    r"^\s*(?:Directions?|说明|注意事项|Part\s+[IVXLC]+|Section\s+[A-Z])",
    re.IGNORECASE,
)

FIELD_PATTERNS: list[tuple[str, str]] = [
    ("stem_translation", r"题干翻译|题目翻译|中文题干|stem\s*translation"),
    ("options_translation", r"选项翻译|选项中文|options\s*translation"),
    ("explanation", r"答案解析|解析|explanation"),
    ("answer", r"正确答案|参考答案|答案|answer|key"),
]
FIELD_RE = re.compile(
    r"^\s*(?:【)?(?P<name>"
    + "|".join(pattern for _, pattern in FIELD_PATTERNS)
    + r")(?:】)?\s*[:：]\s*(?P<value>.*)$",
    re.IGNORECASE,
)
FIELD_SPACE_RE = re.compile(
    r"^\s*(?:【)?(?P<name>"
    + "|".join(pattern for _, pattern in FIELD_PATTERNS)
    + r")(?:】)?\s+(?P<value>.*)$",
    re.IGNORECASE,
)
FIELD_BARE_RE = re.compile(
    r"^\s*(?:【)?(?P<name>"
    + "|".join(pattern for _, pattern in FIELD_PATTERNS)
    + r")(?:】)?\s*$",
    re.IGNORECASE,
)

META_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("id", re.compile(
        r"^(?:文章\s*ID|文章编号|article[_\s-]*id|id)\s*[:：]?\s*(\S+)\s*$",
        re.IGNORECASE,
    )),
    ("year", re.compile(
        r"^(?:年份|考试年份|year)\s*[:：]?\s*(20\d{2})\s*$",
        re.IGNORECASE,
    )),
    ("month", re.compile(
        r"^(?:月份|考试月份|month)\s*[:：]?\s*(\d{1,2})\s*月?\s*$",
        re.IGNORECASE,
    )),
    ("set", re.compile(
        r"^(?:套数|试卷|set)\s*[:：]?\s*(.+)$",
        re.IGNORECASE,
    )),
    ("type", re.compile(
        r"^(?:题型|文章类型|type)\s*[:：]?\s*(.+)$",
        re.IGNORECASE,
    )),
]


class ExtractError(ValueError):
    """用户输入或题库文件的问题，界面只显示友好提示。"""


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------


def normalize_text(raw: str) -> str:
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = text.replace("\u3000", " ")
    return text


def join_text(lines: list[str], chinese: bool = False) -> str:
    text = " ".join(part.strip() for part in lines if part.strip())
    text = re.sub(r"\s+", " ", text).strip()
    if chinese:
        text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    return text


def normalize_set(value: str) -> tuple[str, int]:
    """返回 (“第一套”, 1)；无法识别时默认第一套。"""
    raw = (value or "").strip()
    match = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*套", raw)
    token = match.group(1) if match else raw
    token = token.strip()
    if token.isdigit():
        number = int(token)
    else:
        rev = {v: k for k, v in CN_NUMBERS.items()}
        number = rev.get(token, 1)
    number = max(1, min(number, 99))
    return f"第{CN_NUMBERS.get(number, str(number))}套", number


def normalize_type(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip())
    if not value:
        return "仔细阅读 Passage 1"
    if re.search(r"Passage\s*\d+", value, re.IGNORECASE):
        if not any(keyword in value for keyword in TYPE_KEYWORDS):
            value = "仔细阅读 " + value
    return value


def safe_id(value: str) -> str:
    value = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ExtractError(
            "文章ID只能包含字母、数字、下划线、点和短横线，例如 2025_06_set1_p1。"
        )
    return value


def type_code(type_name: str) -> str:
    match = re.search(r"Passage\s*(\d+)", type_name, re.IGNORECASE)
    if match:
        return f"p{match.group(1)}"
    if "选词填空" in type_name:
        return "cloze"
    if "长篇阅读" in type_name:
        return "long"
    if "信息匹配" in type_name:
        return "match"
    return "careful"


def auto_article_id(year: str, month: str, set_number: int, type_name: str) -> str:
    if year:
        prefix = f"{year}_{month or '00'}_set{set_number}"
    else:
        prefix = f"article_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return f"{prefix}_{type_code(type_name)}"


# ---------------------------------------------------------------------------
# 题型解析
# ---------------------------------------------------------------------------


def _match_field(line: str) -> tuple[str, str] | None:
    match = FIELD_RE.match(line)
    if not match:
        match = FIELD_SPACE_RE.match(line)
    if not match:
        match = FIELD_BARE_RE.match(line)
    if not match:
        return None
    name = match.group("name")
    for key, pattern in FIELD_PATTERNS:
        if re.fullmatch(pattern, name, re.IGNORECASE):
            value = match.groupdict().get("value", "") or ""
            return key, value.strip()
    return None


def _parse_metadata(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    meta: dict[str, str] = {}
    consumed: set[int] = set()

    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.fullmatch(r"【?\s*文章信息\s*】?", stripped):
            consumed.add(index)
            continue
        for key, pattern in META_PATTERNS:
            match = pattern.match(stripped)
            if match:
                value = match.group(1).strip()
                if key == "id":
                    value = safe_id(value)
                elif key == "month" and value.isdigit():
                    value = value.zfill(2)
                meta[key] = value
                consumed.add(index)
                break

    header = "\n".join(lines[:20])
    if "year" not in meta:
        match = re.search(r"(20\d{2})\s*年", header)
        if match:
            meta["year"] = match.group(1)
    if "month" not in meta:
        match = re.search(r"(?:年|^)\s*(\d{1,2})\s*月", header)
        if match:
            meta["month"] = match.group(1).zfill(2)
    if "set" not in meta:
        match = re.search(r"第\s*[一二三四五六七八九十\d]+\s*套", header)
        if match:
            meta["set"] = match.group(0)
    if "type" not in meta:
        for index, line in enumerate(lines[:20]):
            if any(keyword in line for keyword in TYPE_KEYWORDS):
                match = re.search(
                    r"(?:" + "|".join(TYPE_KEYWORDS) + r").*$",
                    line,
                )
                if match:
                    meta["type"] = match.group(0).strip()
                    consumed.add(index)
                    break

    # 去掉纯标题行，例如“2025年6月第一套 仔细阅读 Passage 1”。
    for index, line in enumerate(lines[:20]):
        stripped = line.strip()
        if not stripped:
            continue
        if (
            re.search(r"20\d{2}\s*年", stripped)
            and len(stripped) <= 100
            and (
                "套" in stripped
                or any(keyword in stripped for keyword in TYPE_KEYWORDS)
                or re.search(r"\d{1,2}\s*月", stripped)
            )
        ):
            consumed.add(index)

    remaining = [
        line for index, line in enumerate(lines) if index not in consumed
    ]
    return meta, remaining


def _split_translations(lines: list[str]) -> list[str]:
    text = "\n".join(lines).strip()
    if not text:
        return []
    parts = re.split(r"\n\s*\n", text)
    if len(parts) == 1:
        parts = re.split(
            r"\n(?=\s*(?:第[一二三四五六七八九十\d]+段|Paragraph\s*\d+|\[\d+\]))",
            text,
            flags=re.IGNORECASE,
        )
    translations: list[str] = []
    for part in parts:
        part = re.sub(
            r"^\s*(?:第[一二三四五六七八九十\d]+段|Paragraph\s*\d+|\[\d+\])"
            r"\s*[:：]?\s*",
            "",
            part,
            flags=re.IGNORECASE,
        )
        joined = join_text(part.split("\n"), chinese=True)
        if joined:
            translations.append(joined)
    return translations


def _extract_translations(lines: list[str]) -> tuple[list[str], list[str]]:
    for index, line in enumerate(lines):
        match = TRANSLATION_LABEL_RE.match(line)
        if not match:
            continue
        block: list[str] = []
        inline = match.group(1).strip()
        if inline:
            block.append(inline)
        block.extend(lines[index + 1 :])
        return _split_translations(block), lines[:index]
    return [], lines


def _clean_passage(lines: list[str]) -> str:
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append("")
            continue
        if SECTION_LABEL_RE.fullmatch(stripped):
            continue
        if re.fullmatch(r"【?\s*题目\s*】?", stripped):
            break
        if DIRECTION_RE.match(stripped):
            continue
        if re.fullmatch(r"[-=_*—]{3,}", stripped):
            continue
        cleaned.append(stripped)

    blocks = re.split(r"\n\s*\n", "\n".join(cleaned))
    paragraphs: list[str] = []
    for block in blocks:
        paragraph = re.sub(r"\s*\n\s*", " ", block)
        paragraph = re.sub(r"[ \t]+", " ", paragraph).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


def _find_question_start(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if QUESTION_MARKER_RE.match(line):
            return index + 1
    for index, line in enumerate(lines):
        if not QUESTION_RE.match(line):
            continue
        window = "\n".join(lines[index : index + 15])
        if (
            len(
                re.findall(
                    r"(?:^|\s)[A-D]\s*[.、．:：)]",
                    window,
                    re.MULTILINE,
                )
            )
            >= 2
        ):
            return index
    return None


def _split_inline_options(line: str) -> list[str]:
    if not re.match(r"^\s*A\s*[.、．:：)]", line, re.IGNORECASE):
        return [line]
    if not re.search(r"[B-D]\s*[.、．:：)]", line, re.IGNORECASE):
        return [line]
    return [
        part.strip()
        for part in re.split(r"(?=[A-D]\s*[.、．:：)])", line)
        if part.strip()
    ]


def _parse_options(
    lines: list[str],
    allow_plain: bool = False,
) -> dict[str, str]:
    options: dict[str, str] = {}
    current: str | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        for part in _split_inline_options(line):
            match = OPTION_RE.match(part)
            if match:
                current = match.group(1).upper()
                options[current] = match.group(2).strip()
            elif current:
                options[current] = join_text([options[current], part], chinese=True)
    if allow_plain and not options:
        plain = [
            line.strip()
            for line in lines
            if line.strip()
        ]
        if len(plain) == 4:
            options = dict(zip(OPTION_LETTERS, plain))
    return options


def _parse_question_block(
    block: list[str],
    number: int,
    first_stem: str,
) -> dict[str, Any]:
    main_lines: list[str] = []
    fields: dict[str, list[str]] = {}
    current_field: str | None = None

    first_stem = first_stem.strip()
    if first_stem:
        # 有些排版把题干和 A-D 选项写在同一行。
        inline_option = re.search(r"(?<!\w)([A-D])\s*[.、．:：)]", first_stem)
        if inline_option and inline_option.start() > 0:
            main_lines.append(first_stem[: inline_option.start()].strip())
            main_lines.append(first_stem[inline_option.start() :].strip())
        else:
            main_lines.append(first_stem)

    for line in block:
        matched = _match_field(line)
        if matched:
            current_field, value = matched
            fields.setdefault(current_field, []).append(value)
        elif current_field:
            fields[current_field].append(line)
        else:
            main_lines.append(line)

    stem_parts: list[str] = []
    option_lines: list[str] = []
    in_options = False
    for line in main_lines:
        for part in _split_inline_options(line):
            if OPTION_RE.match(part):
                in_options = True
            if in_options:
                option_lines.append(part)
            else:
                stem_parts.append(part)

    stem = join_text(stem_parts)
    if not stem:
        raise ExtractError(f"第 {number} 题缺少题干。")
    options = _parse_options(option_lines)

    missing = [letter for letter in OPTION_LETTERS if not options.get(letter)]
    if missing:
        raise ExtractError(
            f"第 {number} 题选项不完整，缺少：{', '.join(missing)}。"
            "请用 A. / B. / C. / D. 四行分别写选项。"
        )

    answer_blob = " ".join(fields.get("answer", []))
    answer_match = re.search(r"\b([A-D])\b", answer_blob, re.IGNORECASE)
    if not answer_match:
        raise ExtractError(
            f"第 {number} 题缺少答案。请写一行：答案：B（或 Answer: B）。"
        )
    answer = answer_match.group(1).upper()

    explanation = join_text(fields.get("explanation", []))
    stem_translation = join_text(fields.get("stem_translation", []), chinese=True)
    option_translations = _parse_options(
        fields.get("options_translation", []),
        allow_plain=True,
    )

    return {
        "id": f"q{number}",
        "stem": stem,
        "options": {letter: options[letter] for letter in OPTION_LETTERS},
        "answer": answer,
        "explanation": explanation,
        "stem_translation": stem_translation,
        "options_translation": {
            letter: option_translations.get(letter, "") for letter in OPTION_LETTERS
        },
    }


def _parse_questions(lines: list[str]) -> list[dict[str, Any]]:
    starts = [
        index
        for index, line in enumerate(lines)
        if QUESTION_RE.match(line) and not QUESTION_MARKER_RE.match(line)
    ]
    if not starts:
        raise ExtractError(
            "没有找到题目。每道题需以“1. 题干”开头，"
            "选项用 A. B. C. D. 四行排列。"
        )

    questions: list[dict[str, Any]] = []
    for order, start in enumerate(starts, start=1):
        end = starts[order] if order < len(starts) else len(lines)
        block = lines[start:end]
        match = QUESTION_RE.match(block[0])
        assert match is not None
        first_stem = match.group(2).strip()
        questions.append(
            _parse_question_block(block[1:], order, first_stem)
        )
    return questions


# ---------------------------------------------------------------------------
# 数据组装、校验与写盘
# ---------------------------------------------------------------------------


def parse_article_text(
    raw: str,
    existing_ids: set[str] | None = None,
) -> dict[str, Any]:
    text = normalize_text(raw)
    if not text.strip():
        raise ExtractError("文本框是空的，请先粘贴真题文本。")

    lines = text.split("\n")
    meta, remaining = _parse_metadata(lines)

    # 先定位题目区域，再从文章区域里剥离“段落翻译”，避免把题目误当译文。
    question_start = _find_question_start(remaining)
    if question_start is None:
        raise ExtractError(
            "没有找到题目区域。请在文章后面用“1. 题干”开始题目，"
            "并在题目下写 A. B. C. D. 四个选项。"
        )
    body_lines = [
        line
        for line in remaining[:question_start]
        if not QUESTION_MARKER_RE.match(line)
    ]
    question_lines = remaining[question_start:]
    translations, body_lines = _extract_translations(body_lines)

    passage = _clean_passage(body_lines)
    if not passage:
        raise ExtractError("没有找到文章正文。请在“【文章】”后粘贴文章。")

    questions = _parse_questions(question_lines)
    if not questions:
        raise ExtractError("题目区域没有解析出任何题目。")

    year = meta.get("year") or str(datetime.now().year)
    month = meta.get("month") or "00"
    set_name, set_number = normalize_set(meta.get("set", "第一套"))
    type_name = normalize_type(meta.get("type", "仔细阅读 Passage 1"))

    explicit_id = meta.get("id")
    existing = {value for value in (existing_ids or set())}
    if explicit_id:
        article_id = safe_id(explicit_id)
        if article_id in existing:
            raise ExtractError(
                f"文章ID已存在：{article_id}。请换一个ID，或删除文章信息里的文章ID让程序自动生成。"
            )
    else:
        article_id = auto_article_id(year, month, set_number, type_name)
        base_id = article_id
        suffix = 2
        while article_id in existing:
            article_id = f"{base_id}_{suffix}"
            suffix += 1

    article = {
        "id": article_id,
        "year": year,
        "month": month,
        "set": set_name,
        "type": type_name,
        "passage": passage,
        "passage_translation": translations,
        "questions": questions,
    }
    validate_article(article)
    return article


def validate_article(article: dict[str, Any]) -> None:
    if not isinstance(article, dict):
        raise ExtractError("解析结果不是 JSON 对象。")
    for key in ("id", "year", "month", "set", "type", "passage"):
        if not isinstance(article.get(key), str) or not article[key].strip():
            raise ExtractError(f"字段 {key} 不能为空。")
    safe_id(article["id"])
    if "::" in article["id"]:
        raise ExtractError("文章ID不能包含 ::。")

    translations = article.get("passage_translation")
    if not isinstance(translations, list) or not all(
        isinstance(item, str) for item in translations
    ):
        raise ExtractError("passage_translation 必须是字符串数组。")

    questions = article.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ExtractError("文章至少需要一道题。")
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            raise ExtractError(f"第 {index} 题格式错误。")
        if not question.get("stem"):
            raise ExtractError(f"第 {index} 题缺少题干。")
        options = question.get("options")
        if not isinstance(options, dict) or set(options) != set(OPTION_LETTERS):
            raise ExtractError(f"第 {index} 题必须包含 A-D 四个选项。")
        if any(not isinstance(options[letter], str) or not options[letter].strip() for letter in OPTION_LETTERS):
            raise ExtractError(f"第 {index} 题存在空选项。")
        if question.get("answer") not in OPTION_LETTERS:
            raise ExtractError(f"第 {index} 题答案必须是 A/B/C/D。")
        if not isinstance(question.get("explanation"), str):
            raise ExtractError(f"第 {index} 题解析必须是字符串。")
        if not isinstance(question.get("stem_translation"), str):
            raise ExtractError(f"第 {index} 题题干翻译必须是字符串。")
        option_translations = question.get("options_translation")
        if not isinstance(option_translations, dict) or set(option_translations) != set(OPTION_LETTERS):
            raise ExtractError(f"第 {index} 题选项翻译必须包含 A-D 四个字段。")


def _load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else []
    except (OSError, UnicodeError) as exc:
        raise ExtractError(f"无法读取题库文件：{path}\n{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ExtractError(
            f"题库文件不是合法 JSON：{path}\n"
            f"第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}"
        ) from exc
    if not isinstance(data, list):
        raise ExtractError("题库文件顶层必须是 JSON 数组。")
    return data


def append_article(
    raw: str,
    data_path: Path,
) -> tuple[dict[str, Any], int]:
    existing = _load_existing(data_path)
    existing_ids = {
        item.get("id")
        for item in existing
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    article = parse_article_text(raw, existing_ids=existing_ids)
    existing.append(article)

    serialized = json.dumps(existing, ensure_ascii=False, indent=2) + "\n"
    # 先验证最终 JSON，再原子替换，避免写坏题库。
    json.loads(serialized)
    temp_path = data_path.with_name(data_path.name + ".tmp")
    try:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(serialized, encoding="utf-8")
        if data_path.exists():
            try:
                shutil.copy2(data_path, data_path.with_name(data_path.name + ".bak"))
            except OSError:
                pass
        temp_path.replace(data_path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ExtractError(
            f"无法写入题库文件：{data_path}\n{exc}\n"
            "请把工具放到有写入权限的目录，或检查文件是否被占用。"
        ) from exc
    return article, len(existing)


def resolve_data_file(create: bool = False) -> Path:
    """定位题库文件；打包后的 exe 优先使用 exe 同级的 assets。"""
    candidates: list[Path] = []
    if FROZEN:
        candidates.append(EXE_DIR / "assets" / "data" / "articles.json")
        candidates.append(EXE_DIR.parent / "assets" / "data" / "articles.json")
        candidates.append(EXE_DIR / "data" / "articles.json")
    else:
        candidates.append(SOURCE_DIR / "assets" / "data" / "articles.json")

    for candidate in candidates:
        if candidate.exists():
            return candidate

    destination = candidates[0]
    if not create:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    seed_candidates = [
        BUNDLE_DIR / "assets" / "data" / "articles.json",
        SOURCE_DIR / "assets" / "data" / "articles.json",
    ]
    for seed in seed_candidates:
        if seed.exists() and seed.resolve() != destination.resolve():
            try:
                shutil.copy2(seed, destination)
                return destination
            except OSError:
                pass
    destination.write_text("[]\n", encoding="utf-8")
    return destination


# ---------------------------------------------------------------------------
# Flet 界面
# ---------------------------------------------------------------------------


def main(page: ft.Page) -> None:
    page.title = APP_TITLE
    page.padding = 0
    page.spacing = 0
    page.bgcolor = "#F4F0E7"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(font_family="Microsoft YaHei")
    page.window.width = 980
    page.window.height = 780
    page.window.min_width = 760
    page.window.min_height = 600

    try:
        data_path = resolve_data_file(create=True)
    except Exception:
        data_path = SOURCE_DIR / "assets" / "data" / "articles.json"

    status_text = ft.Text(
        "准备就绪。把真题纯文本粘贴到下方，然后点击“提取并追加”。",
        size=12.5,
        color="#4A5568",
    )
    detail_text = ft.Text(
        f"目标题库：{data_path}",
        size=10.5,
        color="#718096",
        selectable=True,
    )
    input_field = ft.TextField(
        multiline=True,
        min_lines=18,
        max_lines=32,
        expand=True,
        text_size=13,
        hint_text=(
            "在这里粘贴真题文本……\n\n"
            "推荐格式：\n"
            "【文章信息】年份：2025  月份：6  套数：第一套\n"
            "【文章】正文段落……\n"
            "【段落翻译】对应段落翻译……\n"
            "【题目】1. 题干 …… A. …… B. …… C. …… D. ……\n"
            "答案：B\n解析：……\n题干翻译：……\n选项翻译：A. ……"
        ),
        border_color="#D8CDBB",
        focused_border_color="#1E3A5F",
        bgcolor="#FFFDF8",
        content_padding=14,
    )

    def set_status(message: str, color: str, detail: str = "") -> None:
        status_text.value = message
        status_text.color = color
        if detail:
            detail_text.value = detail
        page.update()

    def on_clear(_: ft.Event[ft.Control]) -> None:
        input_field.value = ""
        set_status("已清空输入框。", "#4A5568", f"目标题库：{data_path}")

    def on_extract(_: ft.Event[ft.Control]) -> None:
        raw = input_field.value or ""
        if not raw.strip():
            set_status("❌ 文本框是空的，请先粘贴真题文本。", "#C0392B")
            return
        try:
            article, total = append_article(raw, data_path)
        except ExtractError as exc:
            set_status(f"❌ {exc}", "#C0392B", "未写入题库，请修改格式后重试。")
            return
        except Exception:
            set_status(
                "❌ 处理时出现内部错误，题库未被修改。",
                "#C0392B",
                traceback.format_exc(limit=4),
            )
            return

        set_status(
            f"✅ 已追加成功：{article['id']}（题库共 {total} 篇）",
            "#1E7A4B",
            (
                f"文件：{data_path}\n"
                f"年份/月份：{article['year']} / {article['month']}\n"
                f"题型：{article['type']}\n"
                f"题目数：{len(article['questions'])}"
            ),
        )

    extract_button = ft.FilledButton(
        content="提取并追加",
        icon=ft.Icons.UPLOAD_FILE,
        on_click=on_extract,
        bgcolor="#1E3A5F",
        color="#FFFFFF",
        height=44,
    )
    clear_button = ft.OutlinedButton(
        content="清空",
        icon=ft.Icons.CLEAR,
        on_click=on_clear,
        height=44,
    )

    header = ft.Container(
        padding=ft.Padding.only(left=28, right=28, top=20, bottom=16),
        bgcolor="#1E3A5F",
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.AUTO_FIX_HIGH, size=22, color="#E9C46A"),
                        ft.Text(
                            APP_TITLE,
                            size=22,
                            weight=ft.FontWeight.BOLD,
                            color="#FFFFFF",
                        ),
                        ft.Container(expand=True),
                        ft.Container(
                            padding=ft.Padding.symmetric(horizontal=9, vertical=4),
                            bgcolor="#2D4D73",
                            border_radius=ft.BorderRadius.all(20),
                            content=ft.Text(
                                "离线 · 追加模式",
                                size=10,
                                color="#D7E3F4",
                            ),
                        ),
                    ],
                    spacing=9,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text(
                    "粘贴 → 解析 → 校验 → 追加。写入前会备份原题库。",
                    size=11.5,
                    color="#C9D8EA",
                ),
            ],
            spacing=5,
            tight=True,
        ),
    )

    status_panel = ft.Container(
        bgcolor="#FFFDF8",
        border=ft.Border.all(1, "#D8CDBB"),
        border_radius=ft.BorderRadius.all(10),
        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        content=ft.Column(
            controls=[status_text, detail_text],
            spacing=4,
            tight=True,
        ),
    )

    page.add(
        ft.Column(
            controls=[
                header,
                ft.Container(
                    expand=True,
                    padding=ft.Padding.only(left=24, right=24, top=16, bottom=16),
                    content=ft.Column(
                        controls=[
                            ft.Text(
                                "真题纯文本",
                                size=12.5,
                                weight=ft.FontWeight.W_600,
                                color="#2D3748",
                            ),
                            input_field,
                            ft.Row(
                                controls=[
                                    extract_button,
                                    clear_button,
                                    ft.Container(expand=True),
                                    ft.Text(
                                        "A-D 四个选项 + 答案必填",
                                        size=10,
                                        color="#8A7A66",
                                    ),
                                ],
                                spacing=10,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            status_panel,
                        ],
                        spacing=9,
                        expand=True,
                    ),
                ),
            ],
            spacing=0,
            expand=True,
        )
    )


if __name__ == "__main__":
    ft.run(main)
