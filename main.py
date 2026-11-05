# -*- coding: utf-8 -*-
r"""
四级阅读刷题助手（Windows / Android / Flet / 离线优先 + 题库热更新）
=====================================================

一、安装依赖
------------
Android/纯运行环境：

    python -m pip install -r requirements.txt

Windows 桌面开发/打包环境（包含 flet desktop、CLI 和 PyInstaller）：

    python -m pip install -r requirements-desktop.txt

二、资源文件
------------
`flet build` 会把 assets 目录打包进 APK，并注入 FLET_ASSETS_DIR 绝对路径：

    cet4_reading_helper/
    ├── main.py
    ├── assets/
    │   ├── icon.png
    │   ├── dict.db                 # 45,000 词学术核心离线词典
    │   └── data/
    │       └── articles.json       # 文章、题目、答案、解析、翻译
    └── scripts/build_ecdict.py

`articles.json` 除原有字段外，还支持：

    "passage_translation": ["第一段中文", "第二段中文", "..."],
    "questions": [
      {
        "stem_translation": "题干中文翻译",
        "options_translation": {
          "A": "A 选项中文", "B": "...", "C": "...", "D": "..."
        }
      }
    ]

缺少翻译字段时，界面显示“暂无翻译”。

三、运行
--------
    python main.py
    # 或
    flet run main.py

Android APK 云端打包见 .github/workflows/build_apk.yml；也可在已配置好
JDK 17 / Android SDK 的机器上执行：

    flet build apk . --arch arm64-v8a --python-version 3.12 \
        --skip-flutter-doctor --yes

四、重建离线词典
----------------
    python scripts/build_ecdict.py --source path/to/ecdict.csv

脚本会过滤 ECDICT 的 CET4/CET6/GK/ZK 词汇，生成 dictionary、word_forms
和 collocations 表。没有 --source 时会自动下载 ECDICT CSV。
ECDICT: https://github.com/skywind3000/ECDICT (MIT License)

五、题库热更新
--------------
在文件顶部把 UPDATE_URL 换成 articles.json 的公开直链，然后在 App 中点击
“同步最新题库”。首次启动时会通过 StoragePaths 获取应用文档目录；下载的
题库保存为 CET4ReadingHelper/articles.json。下次启动优先读取该文件，只
有不存在或校验失败时才回退到内置 assets。

六、实现要点
------------
1. 每个英文单词由独立 TextSpan 绑定 on_click；点击词卡只更新卡片内容，不
   rebuild 整页，并记录/恢复 ScrollableControl 的 offset，阅读位置不会跳顶。
2. 词卡为正文下方三行窄卡片，短语同时显示英文和中文释义。
3. 每段正文下方有“查看本段翻译”ExpansionTile；解析顶部显示题干和四个选项
   的中文翻译。
4. 作答状态写入 FLET_APP_STORAGE_DATA（本地开发为 .progress/answers.json）；
   目录显示 ✅ 正确完成 / ❌ 完成错误 / ⚪ 未做，已完成文章可“重新做题”清空本篇记录。
5. 答题没有提交按钮：点击 A/B/C/D 后立即判定、着色并展开解析并自动滚动。
6. 热更新下载到应用文档目录，校验成功才原子替换；网络/格式失败只提示，不
   影响当前已加载的题库和做题状态。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import flet as ft
import requests

# ---------------------------------------------------------------------------
# 常量与配色
# ---------------------------------------------------------------------------

APP_TITLE = "四级阅读刷题助手"
APP_DIR = Path(__file__).resolve().parent

# 把这里替换成你的 GitHub Raw / 静态服务器直链。
# 例如：https://raw.githubusercontent.com/USER/REPO/main/assets/data/articles.json
UPDATE_URL = "https://cdn.jsdelivr.net/gh/Le-ash2024/cet4@main/assets/data/articles.json"
UPDATE_TIMEOUT = 30
LOCAL_DATA_DIR_NAME = "CET4ReadingHelper"

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
    ("reading", "n.", "阅读；读物", "reading comprehension：阅读理解；reading habit：阅读习惯"),
    ("habit", "n.", "习惯；习性", "form a habit：养成习惯；reading habit：阅读习惯"),
    ("improve", "v.", "改善；提高", "improve on：改进；improve skills：提高技能"),
    ("language", "n.", "语言；言语", "body language：肢体语言；foreign language：外语"),
    ("knowledge", "n.", "知识；了解", "general knowledge：常识；to one's knowledge：据某人所知"),
    ("attention", "n.", "注意力；关注", "pay attention to：注意；draw attention to：引起注意"),
    ("focus", "v./n.", "集中；焦点", "focus on：专注于；focus attention on：集中注意力于"),
    ("memory", "n.", "记忆；回忆", "from memory：凭记忆；short-term memory：短期记忆"),
    ("routine", "n./adj.", "常规；日常的", "daily routine：日常安排；routine work：日常工作"),
    ("benefit", "n./v.", "好处；受益", "benefit from：从……受益；for the benefit of：为了……的利益"),
    ("practice", "n./v.", "练习；实践", "in practice：实际上；practice makes perfect：熟能生巧"),
    ("information", "n.", "信息；资料", "information about：关于……的信息；a piece of information：一条信息"),
    ("consistent", "adj.", "一致的；持续的", "be consistent with：与……一致；consistent effort：持续努力"),
]

# 高频四六级固定搭配（用于示例词典和 ECDICT 构建脚本）
COLLOCATIONS_SEED: dict[str, list[tuple[str, str]]] = {
    "account": [("account for", "解释；占……比例")],
    "adapt": [("adapt to", "适应")],
    "apply": [("apply for", "申请"), ("apply to", "适用于")],
    "attention": [("pay attention to", "注意；关注")],
    "benefit": [("benefit from", "从……中受益")],
    "break": [("break down", "出故障；分解")],
    "bring": [("bring about", "带来；引起")],
    "carry": [("carry out", "执行；实施")],
    "come": [("come up with", "提出；想出")],
    "concentrate": [("concentrate on", "专注于")],
    "contribute": [("contribute to", "促成；有助于")],
    "cope": [("cope with", "应对；处理")],
    "depend": [("depend on", "依赖；取决于")],
    "devote": [("devote oneself to", "致力于；献身于")],
    "difference": [("make a difference", "产生影响")],
    "effect": [("have an effect on", "对……有影响")],
    "focus": [("focus on", "专注于；集中注意力于")],
    "get": [("get rid of", "摆脱；去除")],
    "give": [("give up", "放弃")],
    "lead": [("lead to", "导致；通向")],
    "make": [("make sense", "有意义；讲得通"), ("make sure", "确保")],
    "participate": [("participate in", "参加")],
    "pay": [("pay attention to", "注意；关注")],
    "refer": [("refer to", "提到；参考")],
    "rely": [("rely on", "依靠；依赖")],
    "result": [("result in", "导致"), ("result from", "由……造成")],
    "set": [("set up", "建立；设置")],
    "take": [("take advantage of", "利用"), ("take measures", "采取措施")],
    "tend": [("tend to", "倾向于")],
    "turn": [("turn out", "结果是；证明是")],
}


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
    stem_translation: str = ""
    options_translation: dict[str, str] = field(default_factory=dict)


@dataclass
class Article:
    aid: str
    year: str
    month: str
    set_name: str
    type: str
    passage: str
    questions: list[Question] = field(default_factory=list)
    passage_translation: list[str] = field(default_factory=list)

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


def resolve_dictionary_path() -> Path:
    """
    完整离线词典固定放在 assets/dict.db。

    Android 生产包通过 FLET_ASSETS_DIR 定位；桌面打包优先使用 exe 同级
    assets/dict.db，其次使用 PyInstaller 临时目录中的资源。
    """
    assets_db = get_assets_dir() / "dict.db"
    candidates: list[Path] = [assets_db]

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.insert(0, exe_dir / "assets" / "dict.db")
        bundle = _bundle_dir()
        if bundle:
            candidates.insert(1, bundle / "assets" / "dict.db")
        candidates.extend(
            [
                exe_dir / "dict.db",
                exe_dir / "data" / "dict.db",
            ]
        )

    # 兼容旧版本把 dict.db 放在 assets/data 或项目 data/ 的情况。
    candidates.extend(
        [
            get_assets_dir() / "data" / "dict.db",
            APP_DIR / "data" / "dict.db",
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return assets_db


async def resolve_writable_data_dir(page: ft.Page) -> Path:
    """
    返回保存热更新题库的可写目录。

    优先使用 Flet 的 get_application_documents_directory()（Android 应用文档
    目录）；若服务不可用，再回退到 Flet 注入的私有存储环境变量。
    """
    try:
        storage = ft.StoragePaths()
        page.update()
        docs = await asyncio.wait_for(
            storage.get_application_documents_directory(),
            timeout=5,
        )
        if docs:
            target = Path(docs) / LOCAL_DATA_DIR_NAME
            target.mkdir(parents=True, exist_ok=True)
            return target
    except Exception:
        # 某些平台/测试环境没有 StoragePaths 服务，继续走环境变量回退。
        pass

    candidates: list[Path] = []
    for env_name in ("FLET_APP_STORAGE_DATA", "FLET_APP_STORAGE_CACHE"):
        env_value = os.environ.get(env_name)
        if env_value:
            candidates.append(Path(env_value) / LOCAL_DATA_DIR_NAME)
    candidates.append(Path.home() / f".{LOCAL_DATA_DIR_NAME.lower()}")
    candidates.append(APP_DIR / ".local_data")

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return APP_DIR / ".local_data"


def resolve_progress_path() -> Path:
    """
    做题记录写到应用私有可写目录。

    Android 生产包由 Flet 注入 FLET_APP_STORAGE_DATA；本地开发回退到项目内
    .progress/ 目录。该文件不在 assets 中，重新打包不会覆盖用户记录。
    """
    env_storage = os.environ.get("FLET_APP_STORAGE_DATA") or os.environ.get(
        "FLET_APP_STORAGE_CACHE"
    )
    base = Path(env_storage) if env_storage else APP_DIR / ".progress"
    base.mkdir(parents=True, exist_ok=True)
    return base / "answers.json"


def load_progress(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return _parse_saved_answers(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return {}


def save_progress(path: Path, answers: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(answers, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
    except OSError:
        # 存储失败不应影响做题。
        pass


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
    dict.db 不存在时创建 dictionary / word_forms / collocations 表，并写入
    20 个示例词。生产环境应使用 scripts/build_ecdict.py 生成完整词典。
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS word_forms (
                form  TEXT NOT NULL,
                lemma TEXT NOT NULL,
                PRIMARY KEY (form, lemma)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS collocations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                word        TEXT NOT NULL,
                phrase      TEXT NOT NULL,
                translation TEXT NOT NULL,
                UNIQUE(word, phrase)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_word_forms_form ON word_forms(form)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_collocations_word ON collocations(word)"
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
        collocation_count = connection.execute(
            "SELECT COUNT(*) FROM collocations"
        ).fetchone()[0]
        if collocation_count == 0:
            rows: list[tuple[str, str, str]] = []
            for word, items in COLLOCATIONS_SEED.items():
                for phrase, translation in items:
                    rows.append((word, phrase, translation))
            connection.executemany(
                """
                INSERT OR IGNORE INTO collocations(word, phrase, translation)
                VALUES (?, ?, ?)
                """,
                rows,
            )
        connection.commit()
    finally:
        connection.close()


def ensure_sample_data(data_dir: Path, dictionary_path: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    ensure_sample_articles(data_dir / "articles.json")
    # Android assets are read-only. Never open an existing bundled DB for writes.
    if not dictionary_path.exists():
        ensure_dictionary(dictionary_path)


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _paragraph_translations(value: Any) -> list[str]:
    """passage_translation 支持数组，或按空行分隔的字符串。"""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        blocks = re.split(r"\n\s*\n", value.replace("\r\n", "\n").strip())
        return [block.strip() for block in blocks if block.strip()]
    return []


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

            raw_options_translation = qitem.get("options_translation")
            options_translation: dict[str, str] = {}
            if isinstance(raw_options_translation, dict):
                for letter in OPTION_LETTERS:
                    options_translation[letter] = _as_text(
                        raw_options_translation.get(letter)
                    )

            questions.append(
                Question(
                    qid=qid,
                    stem=stem,
                    options=options,
                    answer=answer,
                    explanation=explanation,
                    stem_translation=_as_text(qitem.get("stem_translation")),
                    options_translation=options_translation,
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
                passage_translation=_paragraph_translations(
                    item.get("passage_translation")
                ),
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


def load_articles_with_fallback(
    local_path: Path,
    bundled_path: Path,
) -> tuple[list[Article], Path, str]:
    """
    启动时优先读取本地热更新缓存；缓存不存在或损坏则回退到内置 assets。
    返回 (文章列表, 实际使用的路径, 提示文字)。
    """
    if local_path.exists():
        try:
            return load_articles(local_path), local_path, ""
        except (OSError, ValueError, UnicodeError) as exc:
            warning = f"本地题库缓存无效，已使用内置题库：{exc}"
            return load_articles(bundled_path), bundled_path, warning
    return load_articles(bundled_path), bundled_path, ""


def download_articles_to_cache(url: str, target: Path) -> list[Article]:
    """
    下载 articles.json，先写临时文件并用 load_articles 完整校验，
    校验通过后才备份并原子替换本地缓存。
    """
    headers = {
        "User-Agent": "CET4ReadingHelper/2.0 (offline study app)",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    response = requests.get(url, headers=headers, timeout=UPDATE_TIMEOUT)
    response.raise_for_status()
    payload = response.content
    if not payload.strip():
        raise ValueError("服务器返回了空内容。")
    if len(payload) > 20 * 1024 * 1024:
        raise ValueError("下载内容超过 20 MB，已拒绝写入。")

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(target.name + ".download")
    try:
        temp_path.write_bytes(payload)
        articles = load_articles(temp_path)
        if target.exists():
            try:
                shutil.copy2(target, target.with_name(target.name + ".bak"))
            except OSError:
                pass
        temp_path.replace(target)
        return articles
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


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
        self.has_word_forms = False
        self.has_collocations = False
        try:
            self.connection = sqlite3.connect(str(path))
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA query_only = ON")
            tables = {
                str(row["name"])
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.has_word_forms = "word_forms" in tables
            self.has_collocations = "collocations" in tables
        except sqlite3.Error:
            self.connection = None

    def close(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            except sqlite3.Error:
                pass
            self.connection = None

    def _lemma_candidates(self, candidate: str) -> list[str]:
        if not self.has_word_forms or self.connection is None:
            return []
        try:
            rows = self.connection.execute(
                """
                SELECT lemma FROM word_forms
                WHERE form = ? COLLATE NOCASE
                LIMIT 3
                """,
                (candidate,),
            ).fetchall()
            return [str(row["lemma"]) for row in rows if row["lemma"]]
        except sqlite3.Error:
            return []

    def _collocations(self, word: str) -> list[str]:
        if not self.has_collocations or self.connection is None:
            return []
        try:
            rows = self.connection.execute(
                """
                SELECT phrase, translation FROM collocations
                WHERE word = ? COLLATE NOCASE
                ORDER BY id
                LIMIT 3
                """,
                (word,),
            ).fetchall()
        except sqlite3.Error:
            return []
        result: list[str] = []
        for row in rows:
            phrase = str(row["phrase"] or "").strip()
            translation = str(row["translation"] or "").strip()
            if phrase and translation:
                result.append(f"{phrase}：{translation}")
            elif phrase:
                result.append(phrase)
        return result

    def lookup(self, word: str) -> dict[str, str] | None:
        if self.connection is None:
            return None
        candidates = _lookup_candidates(word)
        # ECDICT 的 exchange 字段生成的词形表优先于词形本身和后缀规则，
        # 这样 running 会优先命中原形 run；但 be 动词等基础词保留自身释义。
        lemma_candidates: list[str] = []
        if word.strip().lower() not in {
            "am", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did",
        }:
            for candidate in list(candidates):
                for lemma in self._lemma_candidates(candidate):
                    if lemma not in lemma_candidates:
                        lemma_candidates.append(lemma)
        ordered_candidates = lemma_candidates + [
            candidate for candidate in candidates if candidate not in lemma_candidates
        ]

        for candidate in ordered_candidates:
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
                entry_word = str(row["word"] or candidate)
                collocations = self._collocations(entry_word)
                phrases = (
                    "；".join(collocations[:2])
                    if collocations
                    else _format_phrases(row["phrases"])
                )
                return {
                    "word": entry_word,
                    "pos": str(row["pos"] or "—").strip() or "—",
                    "translation": _format_core_translation(row["translation"]),
                    "phrases": phrases,
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
    def __init__(
        self,
        page: ft.Page,
        articles: list[Article],
        dictionary: DictionaryStore,
        data_dir: Path,
        progress_path: Path | None = None,
        initial_answers: dict[str, str] | None = None,
        local_articles_path: Path | None = None,
        sync_message: str = "",
    ):
        self.page = page
        self.articles = articles
        self.dictionary = dictionary
        self.data_dir = data_dir
        self.progress_path = progress_path
        self.local_articles_path = local_articles_path
        self.sync_in_progress = False
        self.sync_message = sync_message or "题库支持在线同步"
        self.sync_button: ft.Control | None = None
        self.sync_status_text: ft.Text | None = None
        self.reset_button: ft.Control | None = None

        self.article_by_id = {article.aid: article for article in articles}
        self.current_article_id = articles[0].aid if articles else None
        # key: article_id::question_id -> A/B/C/D
        self.answers: dict[str, str] = dict(initial_answers or {})

        self.word_card_holder: ft.Container | None = None
        self.progress_text: ft.Text | None = None
        self.shell: ft.Container | None = None
        self.questions_scroll: ft.Column | None = None
        self.mobile_scroll: ft.Column | None = None
        self.article_scroll: ft.Column | None = None
        self.article_scroll_offset = 0.0
        self.mobile_scroll_offset = 0.0
        self._scroll_guard = False
        self.sidebar_scroll: ft.Column | None = None
        self.chips_scroll: ft.Row | None = None
        self.sidebar_scroll_offset = 0.0
        self.chips_scroll_offset = 0.0
        self._nav_scroll_guard = False
        self.status_labels: dict[str, list[ft.Text]] = {}

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
        # 重建会替换目录控件，先挡住重建过程中产生的 0 偏移事件。
        self._nav_scroll_guard = True
        self.page.controls.clear()
        self.mobile_scroll = None
        self.questions_scroll = None
        self.article_scroll = None
        self.sidebar_scroll = None
        self.chips_scroll = None
        self.status_labels = {}
        self.article_scroll_offset = 0.0
        self.mobile_scroll_offset = 0.0
        self._scroll_guard = False
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
        self.page.run_task(self._restore_nav_scroll)

    def _build_reset_button(self, compact: bool = False) -> ft.Control:
        article = self.article_by_id.get(self.current_article_id or "")
        button = ft.OutlinedButton(
            content="重新做题",
            icon=ft.Icons.RESTART_ALT,
            tooltip="清空本篇答题记录，恢复未作答状态",
            on_click=lambda _: self._reset_article(self.current_article_id or ""),
            disabled=not (article is not None and self._has_answers(article)),
            height=32 if compact else 36,
            style=ft.ButtonStyle(
                color=RED,
                side=ft.BorderSide(1, "#E3AAA5"),
                padding=ft.Padding.symmetric(
                    horizontal=8 if compact else 12,
                    vertical=3,
                ),
            ),
        )
        self.reset_button = button
        return button

    def _refresh_reset_button(self) -> None:
        article = self.article_by_id.get(self.current_article_id or "")
        if self.reset_button is not None:
            self.reset_button.disabled = not (
                article is not None and self._has_answers(article)
            )

    def _on_sidebar_scroll(self, event: ft.OnScrollEvent) -> None:
        if not self._nav_scroll_guard:
            self.sidebar_scroll_offset = float(event.pixels or 0.0)

    def _on_chips_scroll(self, event: ft.OnScrollEvent) -> None:
        if not self._nav_scroll_guard:
            self.chips_scroll_offset = float(event.pixels or 0.0)

    async def _scroll_nav_to_saved(self) -> None:
        try:
            if self.sidebar_scroll is not None and self.sidebar_scroll_offset:
                await self.sidebar_scroll.scroll_to(
                    offset=self.sidebar_scroll_offset,
                    duration=0,
                )
            if self.chips_scroll is not None and self.chips_scroll_offset:
                await self.chips_scroll.scroll_to(
                    offset=self.chips_scroll_offset,
                    duration=0,
                )
        except Exception:
            pass

    async def _restore_nav_scroll(self) -> None:
        """页面重建后恢复目录列表/文章横向条的滚动位置。"""
        await asyncio.sleep(0.05)
        await self._scroll_nav_to_saved()
        await asyncio.sleep(0.2)
        self._nav_scroll_guard = False

    def _sync_status_color(self) -> str:
        message = self.sync_message
        if message.startswith("✅"):
            return GREEN
        if message.startswith("❌"):
            return RED
        if message.startswith(("⚠️", "正在")):
            return GOLD
        return "#9FB3CC"

    def _build_sync_button(self, compact: bool) -> ft.Control:
        button = ft.TextButton(
            content="同步题库" if compact else "同步最新题库",
            icon=ft.Icons.SYNC,
            icon_color=GOLD,
            tooltip="同步最新题库",
            on_click=self._on_sync_click,
            disabled=self.sync_in_progress,
            style=ft.ButtonStyle(
                color=GOLD,
                overlay_color="#2A4264",
            ),
        )
        self.sync_button = button
        return button

    def _build_sync_status_text(self) -> ft.Text:
        self.sync_status_text = ft.Text(
            self.sync_message,
            size=9.5,
            color=self._sync_status_color(),
            max_lines=3,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        return self.sync_status_text

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
                                        "刷题助手 · 本地优先",
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
                    ft.Container(
                        margin=ft.Margin.only(top=2),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                        bgcolor=NAVY_SOFT,
                        border_radius=ft.BorderRadius.all(10),
                        content=ft.Column(
                            controls=[
                                self._build_sync_button(compact=False),
                                self._build_sync_status_text(),
                            ],
                            spacing=2,
                            tight=True,
                        ),
                    ),
                    ft.Divider(height=1, thickness=1, color=NAVY_LINE),
                    self._build_sidebar_scroll(nav_controls),
                    ft.Divider(height=1, thickness=1, color=NAVY_LINE),
                    ft.Container(
                        padding=ft.Padding.only(top=12),
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.LOCK_OUTLINE, size=13, color="#8FA3BE"),
                                ft.Text(
                                    "数据本地优先 · 支持在线同步",
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

    def _build_sidebar_scroll(self, nav_controls: list[ft.Control]) -> ft.Column:
        self.sidebar_scroll = ft.Column(
            controls=nav_controls,
            spacing=4,
            scroll=ft.ScrollMode.AUTO,
            on_scroll=self._on_sidebar_scroll,
            expand=True,
        )
        return self.sidebar_scroll

    def _article_answer_keys(self, article: Article) -> list[str]:
        return [f"{article.aid}::{question.qid}" for question in article.questions]

    def _article_status(self, article: Article) -> tuple[str, str, str]:
        """返回 (图标, 文字, 颜色)，用于首页目录标记。"""
        keys = self._article_answer_keys(article)
        selected = [(key, self.answers[key]) for key in keys if key in self.answers]
        if not selected:
            return "⚪", "未做", MUTED
        questions = {question.qid: question for question in article.questions}
        for key, answer in selected:
            qid = key.split("::", 1)[1]
            question = questions.get(qid)
            if question is not None and answer != question.answer:
                return "❌", "完成错误", RED
        if len(selected) == len(keys):
            return "✅", "正确完成", GREEN
        return "⚪", "未做", MUTED

    def _register_status_label(self, article_id: str, label: ft.Text) -> None:
        self.status_labels.setdefault(article_id, []).append(label)

    def _refresh_article_markers(self, article_id: str) -> None:
        article = self.article_by_id.get(article_id)
        if article is None:
            return
        icon, text, color = self._article_status(article)
        for label in self.status_labels.get(article_id, []):
            label.value = f"{icon} {text}"
            label.color = color

    def _build_nav_row(self, article: Article) -> ft.Control:
        active = article.aid == self.current_article_id
        icon, status_text, status_color = self._article_status(article)
        status_label = ft.Text(
            f"{icon} {status_text}",
            size=9.5,
            color=status_color,
            weight=ft.FontWeight.W_600,
        )
        self._register_status_label(article.aid, status_label)

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
                            status_label,
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
            icon, status_text, status_color = self._article_status(item)
            status_label = ft.Text(
                f"{icon} {status_text}",
                size=8.5,
                color="#E8F5EE" if active and status_color == GREEN else status_color,
                weight=ft.FontWeight.W_600,
            )
            self._register_status_label(item.aid, status_label)
            chips.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                    bgcolor=NAVY if active else "#EFE6D6",
                    border_radius=ft.BorderRadius.all(20),
                    on_click=lambda _, aid=item.aid: self._select_article(aid),
                    content=ft.Column(
                        controls=[
                            status_label,
                            ft.Text(
                                f"{item.year} {item.set_name} · {item.type}",
                                size=10.5,
                                color="#FFFFFF" if active else GOLD_DARK,
                                weight=ft.FontWeight.W_600,
                                max_lines=1,
                            ),
                        ],
                        spacing=0,
                        tight=True,
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
                    ft.Text("本地优先", size=9.5, color=GREEN),
                ],
                spacing=3,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        self.chips_scroll = ft.Row(
            controls=chips,
            spacing=7,
            scroll=ft.ScrollMode.AUTO,
            on_scroll=self._on_chips_scroll,
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
                            self._build_sync_button(compact=True),
                            offline,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._build_sync_status_text(),
                    ft.Row(
                        controls=[
                            ft.Text(
                                f"{article.year} 年 · {article.month} 月 · {article.set_name}",
                                size=10.5,
                                color=GOLD_DARK,
                                weight=ft.FontWeight.W_600,
                            ),
                            ft.Container(expand=True),
                            self._build_reset_button(compact=True),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self.chips_scroll,
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
            on_scroll=self._on_mobile_scroll,
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
                    self._build_reset_button(compact=False),
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=12, vertical=7),
                        bgcolor="#EBF3EA",
                        border_radius=ft.BorderRadius.all(20),
                        border=ft.Border.all(1, "#D4E4D2"),
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.LOCK_OUTLINE, size=13, color=GREEN),
                                ft.Text(
                                    "本地优先",
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
        for index, paragraph in enumerate(passage_paragraphs(article.passage)):
            translation = (
                article.passage_translation[index]
                if index < len(article.passage_translation)
                else ""
            )
            translation_tile = ft.ExpansionTile(
                title=ft.Text(
                    "查看本段翻译",
                    size=10.5,
                    color=GOLD_DARK,
                    weight=ft.FontWeight.W_600,
                ),
                controls=[
                    ft.Container(
                        bgcolor=AMBER_BG,
                        border_radius=ft.BorderRadius.all(8),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                        content=ft.Text(
                            translation or "暂无翻译",
                            size=12,
                            color="#4B3B20",
                        ),
                    )
                ],
                tile_padding=ft.Padding.symmetric(horizontal=4, vertical=0),
                controls_padding=ft.Padding.only(left=4, right=4, bottom=8),
                maintain_state=False,
            )
            paragraphs.append(
                ft.Column(
                    controls=[
                        ft.Text(
                            spans=self._build_word_spans(paragraph),
                            size=16.5 if mobile else 17,
                            color=INK,
                            font_family="Georgia",
                            selectable=False,
                            text_align=ft.TextAlign.LEFT,
                        ),
                        translation_tile,
                    ],
                    spacing=2,
                    tight=True,
                )
            )

        article_body = ft.Column(
            controls=paragraphs,
            spacing=12 if mobile else 13,
            scroll=None if mobile else ft.ScrollMode.AUTO,
            expand=not mobile,
            on_scroll=None if mobile else self._on_article_scroll,
        )
        if not mobile:
            self.article_scroll = article_body

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
                    article_body,
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
                    ft.Container(
                        bgcolor="#FFF8E6",
                        border_radius=ft.BorderRadius.all(7),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                        content=ft.Column(
                            controls=[
                                ft.Text(
                                    "题目翻译",
                                    size=10.5,
                                    weight=ft.FontWeight.BOLD,
                                    color=GOLD_DARK,
                                ),
                                ft.Text(
                                    question.stem_translation or "暂无翻译",
                                    size=11.5,
                                    color="#4B3B20",
                                ),
                                *[
                                    ft.Text(
                                        f"{letter}. "
                                        f"{question.options_translation.get(letter) or '暂无翻译'}",
                                        size=11.5,
                                        color="#5A4A2A",
                                    )
                                    for letter in OPTION_LETTERS
                                ],
                            ],
                            spacing=3,
                            tight=True,
                        ),
                    ),
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
                spacing=6,
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

            self._refresh_article_markers(article.aid)
            self._refresh_reset_button()
            self._save_answers()
            self._nav_scroll_guard = True
            self.page.update()
            self.page.run_task(self._restore_nav_scroll)
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

    def _has_answers(self, article: Article) -> bool:
        return any(key in self.answers for key in self._article_answer_keys(article))

    def _save_answers(self) -> None:
        if self.progress_path is not None:
            save_progress(self.progress_path, self.answers)

    def _reset_article(self, article_id: str) -> None:
        article = self.article_by_id.get(article_id)
        if article is None:
            return
        for key in self._article_answer_keys(article):
            self.answers.pop(key, None)
        self._refresh_article_markers(article_id)
        self._save_answers()
        self.mount()

    def _on_article_scroll(self, event: ft.OnScrollEvent) -> None:
        if not self._scroll_guard:
            self.article_scroll_offset = float(event.pixels or 0.0)

    def _on_mobile_scroll(self, event: ft.OnScrollEvent) -> None:
        if not self._scroll_guard:
            self.mobile_scroll_offset = float(event.pixels or 0.0)

    async def _restore_scroll_after_word(self, article_offset: float, mobile_offset: float) -> None:
        """词卡显示/切换会触发布局重排，等一帧后恢复原阅读位置。"""
        await asyncio.sleep(0.05)
        try:
            if self._is_mobile():
                if self.mobile_scroll is not None:
                    await self.mobile_scroll.scroll_to(offset=mobile_offset, duration=0)
            elif self.article_scroll is not None:
                await self.article_scroll.scroll_to(offset=article_offset, duration=0)
        except Exception:
            pass
        await self._scroll_nav_to_saved()
        # 避免恢复滚动产生的 on_scroll(0) 覆盖用户原来的位置。
        await asyncio.sleep(0.25)
        self._scroll_guard = False
        self._nav_scroll_guard = False

    def _select_article(self, article_id: str) -> None:
        if article_id == self.current_article_id:
            self._hide_word_card()
            return
        self.current_article_id = article_id
        self.mount()

    def _set_sync_status(self, message: str, color: str) -> None:
        self.sync_message = message
        if self.sync_status_text is not None:
            self.sync_status_text.value = message
            self.sync_status_text.color = color
        if self.sync_button is not None:
            self.sync_button.disabled = self.sync_in_progress
        self.page.update()

    async def _on_sync_click(self, _: ft.Event[ft.Control]) -> None:
        if self.sync_in_progress:
            return

        url = UPDATE_URL.strip()
        if not url.startswith(("http://", "https://")) or url == "https://...":
            self._set_sync_status("⚠️ 请先在 main.py 中填写 UPDATE_URL", GOLD_DARK)
            return
        if self.local_articles_path is None:
            self._set_sync_status("❌ 无法定位本地可写目录", RED)
            return

        self.sync_in_progress = True
        self._set_sync_status("正在更新题库...", GOLD_DARK)

        try:
            articles = await asyncio.to_thread(
                download_articles_to_cache,
                url,
                self.local_articles_path,
            )
            if not articles:
                raise ValueError("下载的题库为空。")

            previous_id = self.current_article_id
            self.articles = articles
            self.article_by_id = {article.aid: article for article in articles}
            self.current_article_id = (
                previous_id
                if previous_id in self.article_by_id
                else articles[0].aid
            )
            self.sync_in_progress = False
            self.sync_message = f"✅ 更新成功，共 {len(articles)} 篇"
            self.mount()
        except requests.RequestException:
            self.sync_in_progress = False
            self._set_sync_status("❌ 更新失败，请检查网络", RED)
        except ValueError:
            self.sync_in_progress = False
            self._set_sync_status("❌ 更新失败：题库格式不正确", RED)
        except OSError:
            self.sync_in_progress = False
            self._set_sync_status("❌ 更新失败：无法保存题库", RED)
        except Exception:
            self.sync_in_progress = False
            self._set_sync_status("❌ 更新失败，请稍后重试", RED)

    def _show_word(self, word: str) -> None:
        entry = self.dictionary.lookup(word)
        display_word = word
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
            if entry["word"].lower() != word.lower():
                display_word = f"{word} → {entry['word']}"

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
                        display_word,
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

        saved_article_offset = self.article_scroll_offset
        saved_mobile_offset = self.mobile_scroll_offset
        self._scroll_guard = True
        self._nav_scroll_guard = True
        self.word_card_holder.visible = True
        self.page.update()
        self.page.run_task(
            self._restore_scroll_after_word,
            saved_article_offset,
            saved_mobile_offset,
        )

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
        saved_article_offset = self.article_scroll_offset
        saved_mobile_offset = self.mobile_scroll_offset
        self._scroll_guard = True
        self._nav_scroll_guard = True
        self.word_card_holder.visible = False
        if self.shell is not None:
            self.shell.on_click = None
        if update:
            self.page.update()
            self.page.run_task(
                self._restore_scroll_after_word,
                saved_article_offset,
                saved_mobile_offset,
            )
        else:
            self._scroll_guard = False
            self._nav_scroll_guard = False

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


def _parse_saved_answers(raw: Any) -> dict[str, str]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value).upper()
        for key, value in data.items()
        if isinstance(key, str)
        and isinstance(value, str)
        and value.upper() in OPTION_LETTERS
    }


async def main(page: ft.Page) -> None:
    configure_page(page)
    data_dir = resolve_data_dir()
    dictionary_path = resolve_dictionary_path()

    # 先把一个轻量加载页挂到 page 上，StoragePaths 服务才能完成注册并响应
    # get_application_documents_directory()；随后会被正式界面替换。
    page.add(
        ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                controls=[
                    ft.ProgressRing(width=28, height=28, stroke_width=3, color=GOLD_DARK),
                    ft.Text("正在加载题库...", size=12, color=MUTED),
                ],
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
        )
    )
    page.update()

    try:
        writable_dir = await resolve_writable_data_dir(page)
    except Exception:
        writable_dir = APP_DIR / ".local_data"
        writable_dir.mkdir(parents=True, exist_ok=True)
    local_articles_path = writable_dir / "articles.json"

    progress_path: Path | None = None
    initial_answers: dict[str, str] = {}
    try:
        progress_path = resolve_progress_path()
        initial_answers = load_progress(progress_path)
    except Exception:
        # 极少数只读环境下不持久化，但不影响做题。
        progress_path = None
        initial_answers = {}

    sync_message = ""
    try:
        ensure_sample_data(data_dir, dictionary_path)
        articles, active_articles_path, sync_message = load_articles_with_fallback(
            local_articles_path,
            data_dir / "articles.json",
        )
        dictionary = DictionaryStore(dictionary_path)
    except Exception as exc:
        detail = (
            f"内置数据目录：{data_dir}\n"
            f"本地缓存目录：{writable_dir}\n"
            f"词典文件：{dictionary_path}\n"
            f"错误：{exc}\n\n"
            f"{traceback.format_exc(limit=4)}"
        )
        show_error_page(page, "数据加载失败", detail)
        return

    app = ReadingAssistant(
        page,
        articles,
        dictionary,
        active_articles_path.parent,
        progress_path=progress_path,
        initial_answers=initial_answers,
        local_articles_path=local_articles_path,
        sync_message=sync_message,
    )
    app.mount()


if __name__ == "__main__":
    if hasattr(ft, "run"):
        ft.run(main)
    else:  # pragma: no cover - 兼容非常旧的 Flet
        ft.app(target=main)
