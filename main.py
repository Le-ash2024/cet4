# -*- coding: utf-8 -*-
r"""
四级阅读刷题助手（Windows 桌面版 / Flet / 完全离线）
=====================================================

一、安装依赖
------------
建议在项目目录中创建虚拟环境后安装（Windows PowerShell）：

    cd cet4_reading_helper
    python -m venv .venv
    .\.venv\Scripts\activate
    python -m pip install -r requirements.txt

如果不用 requirements.txt，也可以直接安装：

    python -m pip install "flet[desktop,cli]==1.0.5"

说明：Flet 1.x 把桌面运行时和打包命令拆成了 extras。只执行 `pip install flet`
可能装不到 `flet-desktop` / `flet-cli`，所以这里安装 desktop + cli 两个 extras
（`requirements.txt` 已写好）。如果你的环境里已经可以正常启动 Flet 桌面窗口，
继续用现有的 Flet 也可以。（本文件同时兼容 `flet run main.py` 和
`python main.py` 两种启动方式。）

二、放置数据文件
----------------
程序启动时读取项目目录下的：

    cet4_reading_helper/
    ├── main.py
    └── data/
        ├── articles.json     # 文章、题目、答案、解析
        └── dict.db           # SQLite 离线词典

两个文件都不存在时，程序会自动生成一份最小示例数据（1 篇文章、3 道题、
20 个常用词），生成位置仍然是上面的 data/ 目录。直接运行即可看到界面。
如果只缺其中一个，也只会补齐缺失的那一个，不会覆盖已有数据。

`articles.json` 必须是 JSON 数组；每篇文章严格包含：

    {
      "id": "2024_12_set1_p1",
      "year": "2024",
      "month": "12",
      "set": "第一套",
      "type": "仔细阅读 Passage 1",
      "passage": "文章正文……",
      "questions": [
        {
          "id": "q1",
          "stem": "题干……",
          "options": {"A": "……", "B": "……", "C": "……", "D": "……"},
          "answer": "B",
          "explanation": "解析……"
        }
      ]
    }

`dict.db` 至少包含一张 dictionary 表：

    CREATE TABLE dictionary (
        word        TEXT PRIMARY KEY,
        pos         TEXT,
        translation TEXT,
        phrases     TEXT
    );

其中 phrases 可以用分号（中英文分号都可以）或换行分隔多个短语。

三、运行
--------
方式 A（推荐）：

    python main.py

方式 B：

    flet run main.py

程序是纯离线桌面应用，不会请求任何在线 API。

四、用 flet pack 打包成 exe
---------------------------
在项目目录中执行（注意 data 必须一起打进去）：

    flet pack main.py ^
        --name CET4ReadingHelper ^
        --product-name "四级阅读刷题助手" ^
        --file-description "离线四级阅读练习工具" ^
        --add-data "data:data"

生成文件在 `dist\CET4ReadingHelper.exe`。

`--add-data` 的 source:destination 是 Flet 文档中的写法。若你使用的旧版
Flet 报路径分隔符错误，把 "data:data" 换成 Windows 版 PyInstaller 常见的
"data;data" 即可。

打包后，程序优先读取 exe 同级的 data/ 目录（方便你以后替换题库和词典）；
如果同级没有 data/，再读取打包进 exe 内部的 data/。因此既可以直接分发
exe，也可以把 data/ 文件夹放在 exe 旁边一起分发。

五、实现要点
------------
1. 文章不是一整块 Text：每个英文单词都由独立的 TextSpan 渲染，并各自绑定
   on_click，所以每个单词都能单独识别、单独查词。
2. 查词结果不是对话框，也不跳页，而是插入在文章面板正下方的三行窄卡片。
   卡片外层包了 GestureDetector，避免点卡片本身时被“点击外部关闭”的逻辑误伤。
3. 页面根 Container 只在词卡显示时临时挂上 on_click，负责“点击卡片外区域”
   收起卡片；卡片隐藏后立刻置回 None，避免抢走单词 TextSpan 的点击。卡片自身
   还有一个 GestureDetector 消费点击，所以点卡片不会误关闭。
4. 答题没有提交按钮：点击 A/B/C/D 后立即判定、着色并展开解析，并自动把解析
   滚动到可视区域；每道题只允许作答一次，防止反复试答案。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import flet as ft

# ---------------------------------------------------------------------------
# 常量与配色
# ---------------------------------------------------------------------------

APP_TITLE = "四级阅读刷题助手"
APP_DIR = Path(__file__).resolve().parent

ARTICLE_TYPES = [
    "仔细阅读 Passage 1",
    "仔细阅读 Passage 2",
    "仔细阅读 Passage 3",
    "仔细阅读 Passage 4",
    "选词填空（完型1）",
]
TYPE_ORDER = {name: index for index, name in enumerate(ARTICLE_TYPES)}
OPTION_LETTERS = ("A", "B", "C", "D")

# 英文单词：允许 don't / it's / mother-in-law 这类形式作为一个完整单词被点击。
WORD_RE = re.compile(r"[A-Za-z]+(?:[’'][A-Za-z]+)*(?:-[A-Za-z]+)*")

# 页面配色（深蓝侧栏 + 米白阅读区 + 琥珀色强调）
NAVY = "#132238"
NAVY_SOFT = "#1B2E49"
NAVY_LINE = "#2A3E5C"
GOLD = "#D9A84E"
GOLD_DARK = "#9C6B18"
PAPER = "#F4F0E8"
CARD = "#FFFFFF"
INK = "#202938"
MUTED = "#687386"
LINE = "#E3DBCE"
SOFT_LINE = "#E8EDF3"
GREEN = "#2F7D5B"
GREEN_BG = "#E8F5EE"
RED = "#B4443C"
RED_BG = "#FBECEA"
AMBER_BG = "#FFF8E6"
AMBER_LINE = "#E7C778"
BLUE_BG = "#EDF3FA"

SIDEBAR_WIDTH = 344


# ---------------------------------------------------------------------------
# 示例数据
# ---------------------------------------------------------------------------

SAMPLE_PASSAGE = """In the past, reading was often seen as a quiet hobby. Today, however, it has become a valuable habit that can improve both language skills and general knowledge. Many students believe that they need a special environment to focus on a book, but research suggests that a simple daily routine matters more than a perfect setting.

A study of college students found that those who read for twenty minutes each day were better able to remember information than those who read for two hours once a week. The reason is not difficult to understand. Regular practice trains attention and makes the brain more patient with complex ideas. Even a short session can be useful if it is consistent.

Digital screens have changed the way we read. We can find information quickly, but we often move from one page to another without thinking deeply. This habit can weaken memory. To benefit from reading, we need to slow down and give the text our full attention. One practical method is to put the phone in another room and keep a notebook nearby. Writing down a few questions before reading can also help us stay focused.

Reading is not only a skill for exams. It is a way to understand people and the world. A person who reads widely is more likely to notice different opinions and to express ideas clearly. The benefit does not appear overnight, but it grows with practice. If you want to build a lasting habit, start with a small goal. Ten pages a day is enough. The important thing is to make reading part of your routine, not a task that you complete once and forget."""

SAMPLE_ARTICLES: list[dict[str, Any]] = [
    {
        "id": "2024_12_set1_p1",
        "year": "2024",
        "month": "12",
        "set": "第一套",
        "type": "仔细阅读 Passage 1",
        "passage": SAMPLE_PASSAGE,
        "questions": [
            {
                "id": "q1",
                "stem": "What does the study mentioned in the passage suggest?",
                "options": {
                    "A": "Reading for two hours once a week is the most effective way.",
                    "B": "A short but regular reading routine works better.",
                    "C": "Students can only focus in a perfect environment.",
                    "D": "Digital reading always weakens a person's memory.",
                },
                "answer": "B",
                "explanation": (
                    "原文明确指出：每天阅读二十分钟的学生，比每周一次读两小时的学生更能记住信息，"
                    "并强调“regular practice”和“consistent”。因此 B（短时间但规律的阅读更有效）正确。"
                    "A 与原文对比关系相反；C 把“许多学生以为需要完美环境”误当成研究结论，原文反而说"
                    "日常习惯比完美环境更重要；D 把“可能削弱记忆”夸大成“总是”，也不正确。"
                ),
            },
            {
                "id": "q2",
                "stem": "Why does the author mention putting the phone in another room?",
                "options": {
                    "A": "To prove that phones are useless for study.",
                    "B": "To give a practical way to read with full attention.",
                    "C": "To suggest that students should buy more notebooks.",
                    "D": "To explain why digital reading is popular.",
                },
                "answer": "B",
                "explanation": (
                    "第三段先指出数字屏幕使人容易快速跳读、思考变浅，随后给出具体做法：把手机放到另一个"
                    "房间、在身边放笔记本，以便“slow down and give the text our full attention”。因此 B "
                    "正确。A 说手机对学习毫无用处，过于绝对；C 把笔记本当成建议的目的，属于过度推断；"
                    "D 与原文对数字阅读的担忧相反。"
                ),
            },
            {
                "id": "q3",
                "stem": "What is the main message of the passage?",
                "options": {
                    "A": "Reading should be limited to exam preparation.",
                    "B": "Digital screens should be avoided completely.",
                    "C": "Reading is a lasting habit that grows with consistent practice.",
                    "D": "Ten pages a day is too small a goal.",
                },
                "answer": "C",
                "explanation": (
                    "全文从“阅读习惯的价值”写到“规律练习的作用”，最后落脚到“make reading part of your "
                    "routine”，强调阅读是一种可以长期培养、随持续练习而增长的习惯，故 C 正确。A 与"
                    "“Reading is not only a skill for exams”相矛盾；B 原文只说需要放慢速度、集中注意力，"
                    "并未要求完全避开屏幕；D 原文说“Ten pages a day is enough”，并不是说目标太小。"
                ),
            },
        ],
    }
]

# (word, pos, translation, phrases)
DICTIONARY_SEED: list[tuple[str, str, str, str]] = [
    ("the", "art.", "这；那", ""),
    ("a", "art.", "一（个）；任一", ""),
    ("to", "prep.", "到；向；对于", ""),
    ("of", "prep.", "……的；属于", ""),
    ("and", "conj.", "和；并且", ""),
    ("in", "prep.", "在……里；在……期间", ""),
    ("is", "v.", "是（be 的第三人称单数）", ""),
    ("reading", "n.", "阅读；读物", "reading comprehension；reading habit"),
    ("habit", "n.", "习惯；习性", "form a habit；reading habit"),
    ("improve", "v.", "改善；提高", "improve on；improve skills"),
    ("language", "n.", "语言；言语", "body language；foreign language"),
    ("knowledge", "n.", "知识；了解", "general knowledge；to one's knowledge"),
    ("attention", "n.", "注意力；关注", "pay attention to；draw attention to"),
    ("focus", "v./n.", "集中；焦点", "focus on；focus attention on"),
    ("memory", "n.", "记忆；回忆", "from memory；short-term memory"),
    ("routine", "n./adj.", "常规；日常的", "daily routine；routine work"),
    ("benefit", "n./v.", "好处；受益", "benefit from；for the benefit of"),
    ("practice", "n./v.", "练习；实践", "in practice；practice makes perfect"),
    ("information", "n.", "信息；资料", "information about；a piece of information"),
    ("consistent", "adj.", "一致的；持续的", "be consistent with；consistent effort"),
]


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class Question:
    qid: str
    stem: str
    options: dict[str, str]
    answer: str
    explanation: str


@dataclass
class Article:
    aid: str
    year: str
    month: str
    set_name: str
    type: str
    passage: str
    questions: list[Question] = field(default_factory=list)

    @property
    def meta(self) -> str:
        return f"{self.year} · {self.month} 月 · {self.set_name}"

    @property
    def full_label(self) -> str:
        return f"{self.year} · {self.set_name} · {self.type}"


# ---------------------------------------------------------------------------
# 路径、示例数据生成、数据加载
# ---------------------------------------------------------------------------


def get_assets_dir() -> Path:
    """
    返回 Flet 的资源目录。

    `flet build` 生成的 Android 生产包会注入 FLET_ASSETS_DIR（绝对路径）；
    本地运行时回退到 main.py 同级的 assets 目录。
    """
    env_assets_dir = os.environ.get("FLET_ASSETS_DIR")
    if env_assets_dir:
        return Path(env_assets_dir).resolve()
    # 等价于 os.path.join(os.path.dirname(__file__), "assets")
    return (APP_DIR / "assets").resolve()


def _bundle_dir() -> Path:
    """PyInstaller/Flet pack 解包后的临时资源目录。"""
    return Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "_MEIPASS", None) else Path()


def resolve_data_dir() -> Path:
    """
    数据目录优先级：
    1. FLET_ASSETS_DIR/data（Android / Flet build 生产包）；
    2. 打包后 exe 同级 assets/data（允许不改 exe 直接替换题库）；
    3. PyInstaller 临时目录 assets/data，或旧版打包的 data/；
    4. 源码运行时 main.py 同级 assets/data。
    """
    assets_data = get_assets_dir() / "data"
    candidates: list[Path] = [assets_data]

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.insert(0, exe_dir / "assets" / "data")
        bundle = _bundle_dir()
        if bundle:
            candidates.insert(1, bundle / "assets" / "data")
            candidates.append(bundle / "data")
        candidates.append(exe_dir / "data")

    # 兼容迁移前仍放在项目根目录的 data/。
    candidates.append(APP_DIR / "data")

    for candidate in candidates:
        if (candidate / "articles.json").exists() or (candidate / "dict.db").exists():
            return candidate

    # 本地开发时允许自动生成；Android 生产包里资源已存在，不会走到这里。
    assets_data.mkdir(parents=True, exist_ok=True)
    return assets_data


def ensure_sample_articles(path: Path) -> None:
    """articles.json 不存在时写入最小示例（不覆盖已有文件）。"""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(SAMPLE_ARTICLES, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ensure_dictionary(path: Path) -> None:
    """
    dict.db 不存在时创建 dictionary 表并写入 20 个常用词。
    如果文件存在但表为空，也会补充示例词条（已有内容不会删除）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dictionary (
                word        TEXT PRIMARY KEY,
                pos         TEXT,
                translation TEXT,
                phrases     TEXT
            )
            """
        )
        count = connection.execute("SELECT COUNT(*) FROM dictionary").fetchone()[0]
        if count == 0:
            connection.executemany(
                """
                INSERT OR IGNORE INTO dictionary(word, pos, translation, phrases)
                VALUES (?, ?, ?, ?)
                """,
                DICTIONARY_SEED,
            )
        connection.commit()
    finally:
        connection.close()


def ensure_sample_data(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    ensure_sample_articles(data_dir / "articles.json")
    ensure_dictionary(data_dir / "dict.db")


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _required_text(item: dict[str, Any], key: str, where: str) -> str:
    value = _as_text(item.get(key))
    if not value:
        raise ValueError(f"{where} 缺少必填字段 {key!r}（必须是非空字符串）。")
    return value


def load_articles(path: Path) -> list[Article]:
    """读取并校验 articles.json，返回按年份/月份/套数/题型排序的文章列表。"""
    if not path.exists():
        raise FileNotFoundError(f"找不到文章数据文件：{path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"articles.json 不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}"
        ) from exc

    if not isinstance(raw, list):
        raise ValueError("articles.json 顶层必须是 JSON 数组（[...]）。")
    if not raw:
        raise ValueError("articles.json 是空数组，请至少放入一篇文章。")

    articles: list[Article] = []
    seen_ids: set[str] = set()

    for article_index, item in enumerate(raw, start=1):
        where = f"第 {article_index} 篇文章"
        if not isinstance(item, dict):
            raise ValueError(f"{where} 必须是 JSON 对象。")

        aid = _required_text(item, "id", where)
        if aid in seen_ids:
            raise ValueError(f"文章 id 重复：{aid!r}。每篇文章的 id 必须唯一。")
        seen_ids.add(aid)

        year = str(item.get("year", "")).strip()
        month = str(item.get("month", "")).strip()
        if not year or not month:
            raise ValueError(f"{where}（{aid}）的 year / month 不能为空。")

        passage = _as_text(item.get("passage"))
        if not passage:
            raise ValueError(f"{where}（{aid}）的 passage 不能为空。")

        raw_questions = item.get("questions")
        if not isinstance(raw_questions, list) or not raw_questions:
            raise ValueError(f"{where}（{aid}）至少需要一道题。")

        questions: list[Question] = []
        seen_qids: set[str] = set()
        for question_index, qitem in enumerate(raw_questions, start=1):
            qwhere = f"{where}（{aid}）的第 {question_index} 题"
            if not isinstance(qitem, dict):
                raise ValueError(f"{qwhere} 必须是 JSON 对象。")

            qid = _required_text(qitem, "id", qwhere)
            if qid in seen_qids:
                raise ValueError(f"{qwhere} 的题目 id 重复：{qid!r}。")
            seen_qids.add(qid)

            stem = _required_text(qitem, "stem", qwhere)
            explanation = _required_text(qitem, "explanation", qwhere)
            options_raw = qitem.get("options")
            if not isinstance(options_raw, dict):
                raise ValueError(f"{qwhere} 的 options 必须是包含 A/B/C/D 的对象。")

            options: dict[str, str] = {}
            for letter in OPTION_LETTERS:
                value = _as_text(options_raw.get(letter))
                if not value:
                    raise ValueError(f"{qwhere} 缺少选项 {letter} 或选项内容为空。")
                options[letter] = value

            answer = str(qitem.get("answer", "")).strip().upper()
            if answer not in OPTION_LETTERS:
                raise ValueError(f"{qwhere} 的 answer 必须是 A/B/C/D 之一，当前为 {answer!r}。")

            questions.append(
                Question(
                    qid=qid,
                    stem=stem,
                    options=options,
                    answer=answer,
                    explanation=explanation,
                )
            )

        articles.append(
            Article(
                aid=aid,
                year=year,
                month=month,
                set_name=_required_text(item, "set", where),
                type=_required_text(item, "type", where),
                passage=passage,
                questions=questions,
            )
        )

    def month_number(value: str) -> int:
        digits = re.sub(r"\D", "", value)
        return int(digits) if digits else 0

    def year_number(value: str) -> int:
        digits = re.sub(r"\D", "", value)
        return int(digits) if digits else 0

    def set_number(value: str) -> int:
        match = re.search(r"第\s*(\d+)\s*套", value)
        if match:
            return int(match.group(1))
        chinese = {
            "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        }
        match = re.search(r"第\s*([一二三四五六七八九十]+)\s*套", value)
        if match:
            text = match.group(1)
            if text == "十":
                return 10
            if text.startswith("十"):
                return 10 + chinese.get(text[1:], 0)
            if "十" in text:
                left, right = text.split("十", 1)
                return chinese.get(left, 0) * 10 + chinese.get(right, 0)
            return chinese.get(text, 99)
        return 99

    articles.sort(
        key=lambda article: (
            -year_number(article.year),
            -month_number(article.month),
            set_number(article.set_name),
            TYPE_ORDER.get(article.type, 99),
            article.type,
            article.aid,
        )
    )
    return articles


def group_articles(articles: Iterable[Article]) -> list[tuple[str, list[tuple[tuple[str, str], list[Article]]]]]:
    """按年份 -> 套数 -> 题型组织数据（返回有序嵌套结构，供左侧导航渲染）。"""
    years: dict[str, dict[tuple[str, str], list[Article]]] = {}
    for article in articles:
        years.setdefault(article.year, {})
        years[article.year].setdefault((article.month, article.set_name), []).append(article)

    result: list[tuple[str, list[tuple[tuple[str, str], list[Article]]]]] = []
    for year, sets in years.items():
        set_groups: list[tuple[tuple[str, str], list[Article]]] = []
        for key, items in sets.items():
            items.sort(key=lambda article: (TYPE_ORDER.get(article.type, 99), article.type, article.aid))
            set_groups.append((key, items))
        result.append((year, set_groups))
    return result


# ---------------------------------------------------------------------------
# 离线词典
# ---------------------------------------------------------------------------


def _lookup_candidates(word: str) -> list[str]:
    """为常见词形变化提供兜底查询，例如 books -> book、studies -> study。"""
    base = word.strip().lower().replace("’", "'")
    base = base.strip("'\".,!?;:()[]{}<>")
    if not base:
        return []

    candidates: list[str] = [base]

    def add(value: str) -> None:
        if value and value not in candidates:
            candidates.append(value)

    if base.endswith("'s"):
        add(base[:-2])
    if base.endswith("s'"):
        add(base[:-2])
    if base.endswith("ies") and len(base) > 3:
        add(base[:-3] + "y")
    if base.endswith("es") and len(base) > 2:
        add(base[:-2])
    if base.endswith("s") and len(base) > 1 and not base.endswith("ss"):
        add(base[:-1])
    if base.endswith("ied") and len(base) > 3:
        add(base[:-3] + "y")
    if base.endswith("ed") and len(base) > 2:
        add(base[:-2])
        add(base[:-1])
    if base.endswith("ing") and len(base) > 3:
        stem = base[:-3]
        add(stem)
        add(stem + "e")
        if len(stem) > 2 and stem[-1] == stem[-2]:
            add(stem[:-1])
    return candidates


def _format_core_translation(value: str) -> str:
    """只保留 1-2 个核心义项。"""
    if not isinstance(value, str):
        return "—"
    parts = [part.strip() for part in re.split(r"[;；\n]+", value) if part.strip()]
    if not parts:
        return "—"
    return "；".join(parts[:2])


def _format_phrases(value: str) -> str:
    if not isinstance(value, str):
        return "—"
    parts = [part.strip(" \t\r\n") for part in re.split(r"[;；\n]+", value) if part.strip()]
    if not parts:
        return "—"
    return "；".join(parts[:2])


class DictionaryStore:
    """只读访问本地 SQLite 词典；任何查询异常都降级为“未收录”，不打断答题。"""

    def __init__(self, path: Path):
        self.path = path
        self.connection: sqlite3.Connection | None = None
        try:
            self.connection = sqlite3.connect(str(path))
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA query_only = ON")
        except sqlite3.Error:
            self.connection = None

    def close(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            except sqlite3.Error:
                pass
            self.connection = None

    def lookup(self, word: str) -> dict[str, str] | None:
        if self.connection is None:
            return None
        for candidate in _lookup_candidates(word):
            try:
                row = self.connection.execute(
                    """
                    SELECT word, pos, translation, phrases
                    FROM dictionary
                    WHERE word = ? COLLATE NOCASE
                    LIMIT 1
                    """,
                    (candidate,),
                ).fetchone()
            except sqlite3.Error:
                return None
            if row is not None:
                return {
                    "word": str(row["word"] or candidate),
                    "pos": str(row["pos"] or "—").strip() or "—",
                    "translation": _format_core_translation(row["translation"]),
                    "phrases": _format_phrases(row["phrases"]),
                }
        return None


# ---------------------------------------------------------------------------
# 文本切分：每个英文单词都是一个独立 TextSpan
# ---------------------------------------------------------------------------


def passage_paragraphs(passage: str) -> list[str]:
    """按空行分段；段落内部的单个换行合并成空格。"""
    normalized = passage.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n\s*\n", normalized)
    paragraphs = [re.sub(r"\s*\n\s*", " ", block).strip() for block in blocks]
    return [paragraph for paragraph in paragraphs if paragraph]


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


# ---------------------------------------------------------------------------
# Flet 界面
# ---------------------------------------------------------------------------


class ReadingAssistant:
    def __init__(self, page: ft.Page, articles: list[Article], dictionary: DictionaryStore, data_dir: Path):
        self.page = page
        self.articles = articles
        self.dictionary = dictionary
        self.data_dir = data_dir

        self.article_by_id = {article.aid: article for article in articles}
        self.current_article_id = articles[0].aid if articles else None
        self.answers: dict[str, str] = {}  # key: article_id::question_id -> A/B/C/D

        self.word_card_holder: ft.Container | None = None
        self.progress_text: ft.Text | None = None
        self.shell: ft.Container | None = None
        self.questions_scroll: ft.Column | None = None
        self.mobile_scroll: ft.Column | None = None

    # ---- 页面骨架 ---------------------------------------------------------

    def _is_mobile(self) -> bool:
        """Android/iOS 或窄窗口使用单列阅读布局。"""
        platform = getattr(self.page, "platform", None)
        if platform in (
            ft.PagePlatform.ANDROID,
            ft.PagePlatform.ANDROID_TV,
            ft.PagePlatform.IOS,
        ):
            return True
        width = getattr(self.page, "width", None)
        return bool(width and width < 760)

    def mount(self) -> None:
        self.page.controls.clear()
        self.mobile_scroll = None
        self.questions_scroll = None
        main_area = self._build_main_area()
        if self._is_mobile():
            content: ft.Control = main_area
        else:
            content = ft.Row(
                controls=[self._build_sidebar(), main_area],
                spacing=0,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )

        self.shell = ft.Container(
            expand=True,
            bgcolor=PAPER,
            on_click=None,
            content=content,
        )

        # 词卡显示时才给根容器挂 on_click。隐藏时 on_click=None，不会和
        # 单词 TextSpan 的手势竞争；显示后点击根容器的任何空白区域都会收起
        # 词卡，而卡片自身的 GestureDetector 优先级更高，点卡片不会误关闭。
        self.page.add(self.shell)
        self.page.update()

    def _build_sidebar(self) -> ft.Control:
        groups = group_articles(self.articles)

        nav_controls: list[ft.Control] = []
        for year, set_groups in groups:
            article_count = sum(len(items) for _, items in set_groups)
            nav_controls.append(
                ft.Container(
                    padding=ft.Padding.only(left=6, right=6, top=12, bottom=4),
                    content=ft.Row(
                        controls=[
                            ft.Text(
                                f"{year} 年",
                                size=20,
                                weight=ft.FontWeight.BOLD,
                                color="#F3F6FA",
                            ),
                            ft.Container(expand=True),
                            ft.Text(
                                f"{article_count} 篇",
                                size=11,
                                color="#93A4BE",
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )

            for (month, set_name), items in set_groups:
                nav_controls.append(
                    ft.Container(
                        padding=ft.Padding.only(left=10, top=8, bottom=4),
                        content=ft.Row(
                            controls=[
                                ft.Container(width=14, height=1, bgcolor="#3B5170"),
                                ft.Text(
                                    f"{month} 月 · {set_name}",
                                    size=11,
                                    weight=ft.FontWeight.W_600,
                                    color="#A9B8CE",
                                ),
                                ft.Container(expand=True),
                            ],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    )
                )

                for article in items:
                    nav_controls.append(self._build_nav_row(article))

        return ft.Container(
            width=SIDEBAR_WIDTH,
            bgcolor=NAVY,
            padding=ft.Padding.only(left=20, right=20, top=24, bottom=16),
            content=ft.Column(
                controls=[
                    # Logo / 标题
                    ft.Row(
                        controls=[
                            ft.Container(
                                width=46,
                                height=46,
                                bgcolor=GOLD,
                                border_radius=ft.BorderRadius.all(14),
                                alignment=ft.Alignment.CENTER,
                                content=ft.Icon(ft.Icons.MENU_BOOK, size=25, color=NAVY),
                            ),
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        "四级阅读",
                                        size=19,
                                        weight=ft.FontWeight.BOLD,
                                        color="#FFFFFF",
                                    ),
                                    ft.Text(
                                        "刷题助手 · 离线版",
                                        size=11,
                                        color="#AFC0D6",
                                    ),
                                ],
                                spacing=1,
                                tight=True,
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(height=20),
                    ft.Row(
                        controls=[
                            ft.Text(
                                "本地题库",
                                size=11,
                                weight=ft.FontWeight.W_600,
                                color="#8FA3BE",
                            ),
                            ft.Container(expand=True),
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                                bgcolor=NAVY_SOFT,
                                border_radius=ft.BorderRadius.all(20),
                                content=ft.Text(
                                    f"{len(self.articles)} 篇",
                                    size=10,
                                    color="#C6D4E5",
                                ),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, thickness=1, color=NAVY_LINE),
                    ft.Column(
                        controls=nav_controls,
                        spacing=4,
                        scroll=ft.ScrollMode.AUTO,
                        expand=True,
                    ),
                    ft.Divider(height=1, thickness=1, color=NAVY_LINE),
                    ft.Container(
                        padding=ft.Padding.only(top=12),
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.LOCK_OUTLINE, size=13, color="#8FA3BE"),
                                ft.Text(
                                    "数据保存在本地 · 全程离线",
                                    size=10,
                                    color="#8FA3BE",
                                ),
                            ],
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ),
                ],
                spacing=8,
                expand=True,
            ),
        )

    def _build_nav_row(self, article: Article) -> ft.Control:
        active = article.aid == self.current_article_id

        def on_click(_: ft.Event[ft.Container], target_id: str = article.aid) -> None:
            self._select_article(target_id)

        row = ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=10),
            border_radius=ft.BorderRadius.all(11),
            bgcolor=NAVY_SOFT if active else None,
            border=ft.Border.all(1, "#3B5274") if active else None,
            on_click=on_click,
            content=ft.Row(
                controls=[
                    ft.Container(
                        width=3,
                        height=38,
                        bgcolor=GOLD if active else None,
                        border_radius=ft.BorderRadius.all(2),
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(
                                article.type,
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color=GOLD if active else "#E8EEF7",
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                f"{article.year} · {article.month} 月 · {article.set_name}",
                                size=10.5,
                                color="#91A5C0" if active else "#8399B7",
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        spacing=2,
                        tight=True,
                        expand=True,
                    ),
                    ft.Icon(
                        ft.Icons.CHEVRON_RIGHT,
                        size=16,
                        color=GOLD if active else "#607A9D",
                    ),
                ],
                spacing=9,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        return row

    def _build_main_area(self) -> ft.Control:
        article = self.article_by_id.get(self.current_article_id or "")
        if article is None:
            return ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                content=ft.Text("题库为空", color=MUTED),
            )

        if self._is_mobile():
            return self._build_mobile_main(article)

        return ft.Column(
            controls=[
                self._build_topbar(article),
                ft.Container(
                    expand=True,
                    padding=ft.Padding.only(left=26, right=26, top=14, bottom=20),
                    content=ft.Column(
                        controls=[
                            self._build_article_panel(article),
                            self._build_word_card_holder(),
                            self._build_question_panel(article),
                        ],
                        spacing=10,
                        expand=True,
                    ),
                ),
            ],
            spacing=0,
            expand=True,
        )

    def _build_mobile_main(self, article: Article) -> ft.Control:
        """手机竖屏：顶部文章切换条 + 单列可滚动正文/词卡/题目。"""
        chips: list[ft.Control] = []
        for item in self.articles:
            active = item.aid == article.aid
            chips.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                    bgcolor=NAVY if active else "#EFE6D6",
                    border_radius=ft.BorderRadius.all(20),
                    on_click=lambda _, aid=item.aid: self._select_article(aid),
                    content=ft.Text(
                        f"{item.year} {item.set_name} · {item.type}",
                        size=10.5,
                        color="#FFFFFF" if active else GOLD_DARK,
                        weight=ft.FontWeight.W_600,
                        max_lines=1,
                    ),
                )
            )

        offline = ft.Container(
            padding=ft.Padding.symmetric(horizontal=9, vertical=4),
            bgcolor="#EBF3EA",
            border_radius=ft.BorderRadius.all(20),
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.LOCK_OUTLINE, size=12, color=GREEN),
                    ft.Text("离线", size=9.5, color=GREEN),
                ],
                spacing=3,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        topbar = ft.Container(
            padding=ft.Padding.only(left=14, right=14, top=12, bottom=10),
            border=ft.Border.only(bottom=ft.BorderSide(1, LINE)),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Text(
                                "四级阅读刷题助手",
                                size=18,
                                weight=ft.FontWeight.BOLD,
                                color=INK,
                            ),
                            ft.Container(expand=True),
                            offline,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        f"{article.year} 年 · {article.month} 月 · {article.set_name}",
                        size=10.5,
                        color=GOLD_DARK,
                        weight=ft.FontWeight.W_600,
                    ),
                    ft.Row(
                        controls=chips,
                        spacing=7,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                ],
                spacing=6,
                tight=True,
            ),
        )

        self.mobile_scroll = ft.Column(
            controls=[
                self._build_article_panel(article, mobile=True),
                self._build_word_card_holder(),
                self._build_question_panel(article, mobile=True),
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        return ft.Column(
            controls=[
                topbar,
                ft.Container(
                    expand=True,
                    padding=ft.Padding.only(left=12, right=12, top=10, bottom=12),
                    content=self.mobile_scroll,
                ),
            ],
            spacing=0,
            expand=True,
        )

    def _build_topbar(self, article: Article) -> ft.Control:
        return ft.Container(
            padding=ft.Padding.only(left=28, right=28, top=16, bottom=14),
            border=ft.Border.only(bottom=ft.BorderSide(1, LINE)),
            content=ft.Row(
                controls=[
                    ft.Column(
                        controls=[
                            ft.Text(
                                f"{article.year} 年  /  {article.month} 月  /  {article.set_name}",
                                size=10.5,
                                color=GOLD_DARK,
                                weight=ft.FontWeight.W_600,
                            ),
                            ft.Row(
                                controls=[
                                    ft.Text(
                                        article.type,
                                        size=22,
                                        weight=ft.FontWeight.BOLD,
                                        color=INK,
                                    ),
                                    ft.Container(
                                        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                                        bgcolor="#EFE6D6",
                                        border_radius=ft.BorderRadius.all(20),
                                        content=ft.Text(
                                            f"{len(article.questions)} 题",
                                            size=10,
                                            color=GOLD_DARK,
                                            weight=ft.FontWeight.W_600,
                                        ),
                                    ),
                                ],
                                spacing=10,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ],
                        spacing=2,
                        tight=True,
                    ),
                    ft.Container(expand=True),
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=12, vertical=7),
                        bgcolor="#EBF3EA",
                        border_radius=ft.BorderRadius.all(20),
                        border=ft.Border.all(1, "#D4E4D2"),
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.LOCK_OUTLINE, size=13, color=GREEN),
                                ft.Text(
                                    "完全离线",
                                    size=10.5,
                                    color=GREEN,
                                    weight=ft.FontWeight.W_600,
                                ),
                            ],
                            spacing=5,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_article_panel(self, article: Article, mobile: bool = False) -> ft.Control:
        paragraphs: list[ft.Control] = []
        for paragraph in passage_paragraphs(article.passage):
            paragraphs.append(
                ft.Text(
                    spans=self._build_word_spans(paragraph),
                    size=16.5 if mobile else 17,
                    color=INK,
                    font_family="Georgia",
                    selectable=False,
                    text_align=ft.TextAlign.LEFT,
                )
            )

        return ft.Container(
            expand=not mobile,
            bgcolor=CARD,
            border_radius=ft.BorderRadius.all(18),
            border=ft.Border.all(1, LINE),
            padding=ft.Padding.only(
                left=16 if mobile else 26,
                right=16 if mobile else 26,
                top=14 if mobile else 18,
                bottom=14 if mobile else 16,
            ),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.ARTICLE, size=16, color=GOLD_DARK),
                            ft.Text(
                                "PASSAGE",
                                size=10.5,
                                weight=ft.FontWeight.BOLD,
                                color=GOLD_DARK,
                            ),
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                                bgcolor=AMBER_BG,
                                border_radius=ft.BorderRadius.all(20),
                                content=ft.Text(
                                    "点击任意英文单词查词",
                                    size=10,
                                    color="#8B6418",
                                ),
                            ),
                            ft.Container(expand=True),
                            ft.Text(
                                f"{count_words(article.passage)} words",
                                size=10,
                                color=MUTED,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, thickness=1, color=SOFT_LINE),
                    ft.Column(
                        controls=paragraphs,
                        spacing=12 if mobile else 13,
                        scroll=None if mobile else ft.ScrollMode.AUTO,
                        expand=not mobile,
                    ),
                ],
                spacing=10,
                expand=not mobile,
            ),
        )

    def _build_word_spans(self, paragraph: str) -> list[ft.TextSpan]:
        spans: list[ft.TextSpan] = []
        cursor = 0
        for match in WORD_RE.finditer(paragraph):
            if match.start() > cursor:
                spans.append(
                    ft.TextSpan(
                        text=paragraph[cursor:match.start()],
                        style=ft.TextStyle(color=INK),
                    )
                )

            raw_word = match.group(0)

            def on_word_click(_: ft.Event[ft.TextSpan], word: str = raw_word) -> None:
                self._show_word(word)

            spans.append(
                ft.TextSpan(
                    text=raw_word,
                    style=ft.TextStyle(color="#24344D"),
                    on_click=on_word_click,
                )
            )
            cursor = match.end()

        if cursor < len(paragraph):
            spans.append(
                ft.TextSpan(
                    text=paragraph[cursor:],
                    style=ft.TextStyle(color=INK),
                )
            )
        return spans

    def _build_word_card_holder(self) -> ft.Control:
        self.word_card_holder = ft.Container(
            visible=False,
            margin=ft.Margin.only(top=1, bottom=1),
            content=ft.GestureDetector(
                # 消费卡片自身点击，避免根手势把刚弹出的卡片立刻收起。
                on_tap=lambda _: None,
                content=ft.Container(
                    bgcolor=AMBER_BG,
                    border=ft.Border.all(1, AMBER_LINE),
                    border_radius=ft.BorderRadius.all(12),
                    padding=ft.Padding.symmetric(horizontal=18, vertical=11),
                    content=ft.Column(
                        controls=[],
                        spacing=3,
                        tight=True,
                    ),
                ),
            ),
        )
        return self.word_card_holder

    def _build_question_panel(self, article: Article, mobile: bool = False) -> ft.Control:
        answered_count = sum(
            1
            for question in article.questions
            if f"{article.aid}::{question.qid}" in self.answers
        )
        self.progress_text = ft.Text(
            f"已答 {answered_count}/{len(article.questions)}",
            size=10.5,
            color=MUTED,
            weight=ft.FontWeight.W_600,
        )

        question_controls = [
            self._build_question_card(article, question, index)
            for index, question in enumerate(article.questions, start=1)
        ]
        if mobile:
            self.questions_scroll = None
            question_body: ft.Control = ft.Column(
                controls=question_controls,
                spacing=12,
            )
        else:
            self.questions_scroll = ft.Column(
                controls=question_controls,
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            )
            question_body = self.questions_scroll

        return ft.Container(
            expand=not mobile,
            bgcolor=CARD,
            border_radius=ft.BorderRadius.all(18),
            border=ft.Border.all(1, LINE),
            padding=ft.Padding.only(
                left=12 if mobile else 20,
                right=12 if mobile else 20,
                top=12 if mobile else 15,
                bottom=8,
            ),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.QUIZ, size=17, color=GOLD_DARK),
                            ft.Text(
                                "题目练习",
                                size=13,
                                weight=ft.FontWeight.BOLD,
                                color=INK,
                            ),
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                                bgcolor=BLUE_BG,
                                border_radius=ft.BorderRadius.all(20),
                                content=ft.Text(
                                    "点击选项立即判分",
                                    size=10,
                                    color="#536D8C",
                                ),
                            ),
                            ft.Container(expand=True),
                            self.progress_text,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, thickness=1, color=SOFT_LINE),
                    question_body,
                ],
                spacing=8,
                expand=not mobile,
            ),
        )

    def _build_question_card(self, article: Article, question: Question, number: int) -> ft.Control:
        state_key = f"{article.aid}::{question.qid}"
        existing = self.answers.get(state_key)
        option_widgets: dict[str, tuple[ft.Container, ft.Icon, ft.Text]] = {}

        for letter in OPTION_LETTERS:
            badge_text = ft.Text(
                letter,
                size=11,
                weight=ft.FontWeight.BOLD,
                color="#56657A",
            )
            badge = ft.Container(
                width=26,
                height=26,
                bgcolor="#EEF2F7",
                border_radius=ft.BorderRadius.all(13),
                alignment=ft.Alignment.CENTER,
                content=badge_text,
            )
            status_icon = ft.Icon(
                ft.Icons.CHECK_CIRCLE,
                size=17,
                color=GREEN,
                visible=False,
            )
            box = ft.Container(
                bgcolor="#FBFCFE",
                border=ft.Border.all(1, SOFT_LINE),
                border_radius=ft.BorderRadius.all(9),
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                content=ft.Row(
                    controls=[
                        badge,
                        ft.Text(
                            question.options[letter],
                            size=12.5,
                            color="#354154",
                            expand=True,
                        ),
                        status_icon,
                    ],
                    spacing=9,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
            option_widgets[letter] = (box, status_icon, badge_text)

        answer_text = ft.Text("", size=12, weight=ft.FontWeight.W_600)
        feedback_key = ft.ScrollKey(f"feedback_{article.aid}_{question.qid}")
        feedback = ft.Container(
            key=feedback_key,
            visible=False,
            padding=ft.Padding.symmetric(horizontal=10, vertical=7),
            border_radius=ft.BorderRadius.all(8),
            content=answer_text,
        )

        explanation_text = ft.Text(
            question.explanation,
            size=12,
            color="#4C5668",
        )
        explanation_box = ft.Container(
            visible=False,
            padding=ft.Padding.only(left=12, right=12, top=10, bottom=10),
            bgcolor="#F7F9FC",
            border=ft.Border.only(left=ft.BorderSide(3, "#9DB2CB")),
            border_radius=ft.BorderRadius.all(7),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.LIGHTBULB_OUTLINE, size=14, color=GOLD_DARK),
                            ft.Text(
                                "解析",
                                size=10.5,
                                weight=ft.FontWeight.BOLD,
                                color=GOLD_DARK,
                            ),
                        ],
                        spacing=5,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    explanation_text,
                ],
                spacing=4,
                tight=True,
            ),
        )

        def apply_answer_styles(selected: str) -> None:
            for letter, (box, icon, badge_text) in option_widgets.items():
                box.bgcolor = "#FBFCFE"
                box.border = ft.Border.all(1, SOFT_LINE)
                icon.visible = False
                badge_text.color = "#56657A"

                if letter == question.answer:
                    box.bgcolor = GREEN_BG
                    box.border = ft.Border.all(1.2, "#9CCBB3")
                    icon.name = ft.Icons.CHECK_CIRCLE
                    icon.color = GREEN
                    icon.visible = True
                    badge_text.color = GREEN
                elif letter == selected:
                    box.bgcolor = RED_BG
                    box.border = ft.Border.all(1.2, "#E3AAA5")
                    icon.name = ft.Icons.CANCEL
                    icon.color = RED
                    icon.visible = True
                    badge_text.color = RED

        def show_feedback(selected: str) -> None:
            correct = selected == question.answer
            if correct:
                answer_text.value = f"✅ 正确 · 答案 {question.answer}"
                answer_text.color = GREEN
                feedback.bgcolor = "#E4F4EA"
            else:
                answer_text.value = f"❌ 错误 · 正确答案是 {question.answer}"
                answer_text.color = RED
                feedback.bgcolor = "#FBE9E7"
            feedback.visible = True
            explanation_box.visible = True

        if existing is not None:
            apply_answer_styles(existing)
            show_feedback(existing)

        def choose(letter: str) -> None:
            if state_key in self.answers:
                return  # 每题只允许作答一次，避免反复试答案
            self.answers[state_key] = letter
            apply_answer_styles(letter)
            show_feedback(letter)

            if self.word_card_holder is not None and self.word_card_holder.visible:
                self.word_card_holder.visible = False

            if self.progress_text is not None:
                answered_count = sum(
                    1
                    for item in article.questions
                    if f"{article.aid}::{item.qid}" in self.answers
                )
                self.progress_text.value = f"已答 {answered_count}/{len(article.questions)}"

            self.page.update()
            # 把“✅/❌ + 解析”这一块自动滚入可视区域，避免用户还要手动往下找。
            self.page.run_task(self._reveal_explanation, feedback_key)

        for letter, (box, _, _) in option_widgets.items():
            box.on_click = lambda _, chosen=letter: choose(chosen)

        return ft.Container(
            bgcolor="#FFFFFF",
            border=ft.Border.all(1, "#E7DFD2"),
            border_radius=ft.BorderRadius.all(13),
            padding=ft.Padding.symmetric(horizontal=14, vertical=12),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Container(
                                width=28,
                                height=28,
                                bgcolor=NAVY,
                                border_radius=ft.BorderRadius.all(14),
                                alignment=ft.Alignment.CENTER,
                                content=ft.Text(
                                    str(number),
                                    size=11,
                                    weight=ft.FontWeight.BOLD,
                                    color="#FFFFFF",
                                ),
                            ),
                            ft.Text(
                                f"Question {number}",
                                size=10.5,
                                weight=ft.FontWeight.W_600,
                                color=MUTED,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        question.stem,
                        size=13.5,
                        weight=ft.FontWeight.W_600,
                        color=INK,
                    ),
                    ft.Column(
                        controls=[widgets[0] for widgets in option_widgets.values()],
                        spacing=6,
                    ),
                    feedback,
                    explanation_box,
                ],
                spacing=8,
                tight=True,
            ),
        )

    # ---- 交互 -------------------------------------------------------------

    def _select_article(self, article_id: str) -> None:
        if article_id == self.current_article_id:
            self._hide_word_card()
            return
        self.current_article_id = article_id
        self.mount()

    def _show_word(self, word: str) -> None:
        entry = self.dictionary.lookup(word)
        if entry is None:
            pos = "—"
            translation = "暂未收录 · 可在本地词典中补充"
            phrases = "—"
            word_color = "#6C4B11"
        else:
            pos = entry["pos"]
            translation = entry["translation"]
            phrases = entry["phrases"]
            word_color = GOLD_DARK

        assert self.word_card_holder is not None
        card = self.word_card_holder.content
        assert isinstance(card, ft.GestureDetector)
        inner = card.content
        assert isinstance(inner, ft.Container)
        assert isinstance(inner.content, ft.Column)

        inner.content.controls = [
            ft.Row(
                controls=[
                    ft.Text(
                        word,
                        size=16,
                        weight=ft.FontWeight.BOLD,
                        color=word_color,
                        font_family="Georgia",
                    ),
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=7, vertical=2),
                        bgcolor="#F0E1B9",
                        border_radius=ft.BorderRadius.all(6),
                        content=ft.Text(
                            pos,
                            size=10.5,
                            color="#74521A",
                            weight=ft.FontWeight.W_600,
                        ),
                    ),
                    ft.Container(expand=True),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Text(
                translation,
                size=12.5,
                color="#4B3B20",
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            ft.Text(
                f"常见短语：{phrases}",
                size=11.5,
                color="#75613A",
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
        ]
        if self.shell is not None:
            self.shell.on_click = self._handle_outside_tap
        self.word_card_holder.visible = True
        self.page.update()

    async def _reveal_explanation(self, feedback_key: ft.ScrollKey) -> None:
        """等布局展开后，把判分结果和解析一起滚动到可视区域。"""
        await asyncio.sleep(0.08)
        scroller = self.mobile_scroll if self._is_mobile() else self.questions_scroll
        if scroller is None:
            return
        try:
            await scroller.scroll_to(
                scroll_key=feedback_key,
                duration=280,
            )
        except Exception:
            # 滚动只是锦上添花；某些 Flet 版本/窗口状态下失败不影响答题。
            pass

    def _hide_word_card(self, update: bool = True) -> None:
        if self.word_card_holder is None or not self.word_card_holder.visible:
            return
        self.word_card_holder.visible = False
        if self.shell is not None:
            self.shell.on_click = None
        if update:
            self.page.update()

    def _handle_outside_tap(self, _: ft.Event[ft.Container]) -> None:
        self._hide_word_card()


# ---------------------------------------------------------------------------
# 启动与错误页
# ---------------------------------------------------------------------------


def configure_page(page: ft.Page) -> None:
    page.title = APP_TITLE
    page.padding = 0
    page.spacing = 0
    page.bgcolor = PAPER
    page.theme_mode = ft.ThemeMode.LIGHT

    platform = getattr(page, "platform", None)
    mobile = platform in (
        ft.PagePlatform.ANDROID,
        ft.PagePlatform.ANDROID_TV,
        ft.PagePlatform.IOS,
    )

    try:
        page.theme = ft.Theme(
            font_family=None if mobile else "Microsoft YaHei",
            color_scheme_seed=NAVY,
        )
    except Exception:
        # 极少数精简运行环境对 Theme 参数支持不同，不影响主流程。
        pass

    # 桌面窗口尺寸只对桌面平台设置；Android 上设置固定宽度会把布局挤出屏幕。
    if not mobile:
        try:
            page.window.width = 1240
            page.window.height = 860
            page.window.min_width = 1000
            page.window.min_height = 680
        except Exception:
            pass


def show_error_page(page: ft.Page, title: str, detail: str) -> None:
    configure_page(page)
    page.controls.clear()
    page.add(
        ft.Container(
            expand=True,
            bgcolor=PAPER,
            alignment=ft.Alignment.CENTER,
            padding=40,
            content=ft.Container(
                width=760,
                bgcolor=CARD,
                border=ft.Border.all(1, "#E3C9C5"),
                border_radius=ft.BorderRadius.all(16),
                padding=28,
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.ERROR_OUTLINE, color=RED, size=26),
                                ft.Text(
                                    title,
                                    size=19,
                                    weight=ft.FontWeight.BOLD,
                                    color=INK,
                                ),
                            ],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Text(
                            "程序没有启动成功。请按照下面的提示检查数据文件；"
                            "修正后重新运行即可。",
                            size=12.5,
                            color=MUTED,
                        ),
                        ft.Container(
                            bgcolor="#F8F4F2",
                            border_radius=ft.BorderRadius.all(10),
                            padding=14,
                            content=ft.Text(
                                detail,
                                size=11.5,
                                color="#5D3A36",
                                selectable=True,
                            ),
                        ),
                    ],
                    spacing=12,
                    tight=True,
                ),
            ),
        )
    )
    page.update()


def main(page: ft.Page) -> None:
    configure_page(page)
    data_dir = resolve_data_dir()

    try:
        ensure_sample_data(data_dir)
        articles = load_articles(data_dir / "articles.json")
        dictionary = DictionaryStore(data_dir / "dict.db")
    except Exception as exc:
        detail = (
            f"数据目录：{data_dir}\n"
            f"错误：{exc}\n\n"
            f"{traceback.format_exc(limit=4)}"
        )
        show_error_page(page, "数据加载失败", detail)
        return

    app = ReadingAssistant(page, articles, dictionary, data_dir)
    app.mount()


if __name__ == "__main__":
    if hasattr(ft, "run"):
        ft.run(main)
    else:  # pragma: no cover - 兼容非常旧的 Flet
        ft.app(target=main)
