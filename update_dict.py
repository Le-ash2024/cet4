# -*- coding: utf-8 -*-
"""
把 assets/dict.db 升级成约 4.5 万词的学术核心词库。

数据源：ECDICT CSV（skywind3000/ECDICT，MIT License）
- 优先保留 CET4/CET6/考研/托福/雅思/GRE 标签词；
- 再按 ECDICT 的 BNC/COCA 词频排名补足；
- 保持 dictionary / word_forms / collocations / meta 表结构不变；
- 原 dict.db 会备份为 dict.db.bak。

用法：
    python update_dict.py --source build/ecdict.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "build" / "ecdict.csv"
DEFAULT_DB = ROOT / "assets" / "dict.db"
TARGET_WORDS = 45_000

# 复用已有词典构建脚本里的清洗、词形和固定搭配逻辑。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.build_ecdict import (  # noqa: E402
    COLLOCATIONS,
    EXTRA_ENTRIES,
    clean_translation,
    exchange_forms,
    regular_forms,
)

ADVANCED_TAGS = {"cet4", "cet6", "ky", "toefl", "ielts", "gre"}
BASIC_TAGS = {"gk", "zk"}
VALID_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'’\- ]*$")


def parse_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def effective_rank(bnc: int, frq: int, advanced: bool, basic: bool) -> int:
    ranks = [rank for rank in (bnc, frq) if rank > 0]
    rank = min(ranks) if ranks else 0
    if rank:
        return rank
    if advanced:
        return 25_000
    if basic:
        return 50_000
    return 1_000_000


def read_candidates(source: Path) -> list[dict[str, Any]]:
    """读取并去重 ECDICT 词条，保留有中文释义的核心候选。"""
    best: dict[str, dict[str, Any]] = {}
    scanned = 0
    with source.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            scanned += 1
            word = (row.get("word") or "").strip().replace("’", "'")
            lower = word.lower()
            if not lower or not VALID_WORD_RE.match(word):
                continue

            tag = (row.get("tag") or "").lower()
            tokens = set(tag.split())
            advanced = bool(tokens & ADVANCED_TAGS)
            basic = bool(tokens & BASIC_TAGS)
            collins = parse_int(row.get("collins"))
            oxford = parse_int(row.get("oxford"))

            # 先过滤明显不合适的词，避免把全量 70 多万行都放进内存。
            if not (
                advanced
                or basic
                or collins
                or oxford
                or parse_int(row.get("bnc")) > 0
                or parse_int(row.get("frq")) > 0
            ):
                continue

            pos, translation = clean_translation(row.get("translation") or "")
            if not translation:
                continue

            bnc = parse_int(row.get("bnc"))
            frq = parse_int(row.get("frq"))
            rank = effective_rank(bnc, frq, advanced, basic)
            if not (advanced or basic or collins or oxford or rank <= 100_000):
                continue

            candidate = {
                "word": lower,
                "pos": pos,
                "translation": translation,
                "phonetic": (row.get("phonetic") or "").strip(),
                "exchange": (row.get("exchange") or "").strip(),
                "tag": tag,
                "is_cet4": int("cet4" in tokens),
                "is_cet6": int("cet6" in tokens),
                "rank": rank,
                "collins": collins,
                "oxford": oxford,
                "advanced": advanced,
                "basic": basic,
            }
            previous = best.get(lower)
            if previous is None:
                best[lower] = candidate
                continue

            # 同一个 word 可能有多行，保留词频更高/标签更丰富的一行。
            old_key = (
                previous["rank"],
                -previous["collins"],
                -previous["oxford"],
                -len(previous["tag"].split()),
            )
            new_key = (
                candidate["rank"],
                -candidate["collins"],
                -candidate["oxford"],
                -len(candidate["tag"].split()),
            )
            if new_key < old_key:
                best[lower] = candidate

    print(f"扫描 {scanned:,} 行，得到 {len(best):,} 个唯一候选词")
    return list(best.values())


def select_words(candidates: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
    """优先标签词，再按词频排序，截取 target 个词。"""
    candidates.sort(
        key=lambda item: (
            item["rank"],
            0 if item["advanced"] else 1 if item["basic"] else 2,
            -item["collins"],
            -item["oxford"],
            item["word"],
        )
    )
    selected = candidates[:target]

    # 确保示例文章需要的功能词、常见词一定存在，并用人工整理的高质量
    # 基础释义覆盖 ECDICT 中过短或词性不准的释义。
    selected_by_word = {item["word"]: item for item in selected}
    for word, pos, translation, phrases in EXTRA_ENTRIES:
        current = selected_by_word.get(word)
        if current is not None:
            current["pos"] = pos
            current["translation"] = translation
            current["extra_phrases"] = phrases
            continue
        item = {
            "word": word,
            "pos": pos,
            "translation": translation,
            "phonetic": "",
            "exchange": "",
            "tag": "extra",
            "is_cet4": 0,
            "is_cet6": 0,
            "rank": 1_000_000,
            "collins": 0,
            "oxford": 0,
            "advanced": False,
            "basic": False,
            "extra_phrases": phrases,
        }
        selected.append(item)
        selected_by_word[word] = item
    return selected


def open_database(db_path: Path, temp_path: Path) -> sqlite3.Connection:
    if temp_path.exists():
        temp_path.unlink()
    if db_path.exists():
        shutil.copy2(db_path, temp_path)
    connection = sqlite3.connect(str(temp_path))
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    # 兼容极旧的字典库：缺少表时补建，表结构与当前应用一致。
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS dictionary (
            word        TEXT PRIMARY KEY,
            pos         TEXT,
            translation TEXT,
            phrases     TEXT,
            phonetic    TEXT,
            exchange    TEXT,
            tag         TEXT,
            is_cet4     INTEGER NOT NULL DEFAULT 0,
            is_cet6     INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS word_forms (
            form  TEXT NOT NULL,
            lemma TEXT NOT NULL,
            PRIMARY KEY (form, lemma)
        );
        CREATE TABLE IF NOT EXISTS collocations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            word        TEXT NOT NULL,
            phrase      TEXT NOT NULL,
            translation TEXT NOT NULL,
            UNIQUE(word, phrase)
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    return connection


def build_dictionary(
    source: Path,
    db_path: Path,
    target: int,
) -> tuple[int, int, int, int]:
    if not source.exists():
        raise FileNotFoundError(
            f"找不到 ECDICT CSV：{source}\n"
            "可先下载：https://github.com/skywind3000/ECDICT"
        )

    candidates = read_candidates(source)
    selected = select_words(candidates, target)
    if len(selected) < target:
        raise RuntimeError(
            f"候选词不足：只得到 {len(selected)}，目标 {target}"
        )

    # 保留旧库里的固定搭配，并合并脚本内置的高频搭配。
    old_collocations: list[tuple[str, str, str]] = []
    if db_path.exists():
        old_conn = sqlite3.connect(str(db_path))
        try:
            old_collocations = old_conn.execute(
                "SELECT word, phrase, translation FROM collocations"
            ).fetchall()
        except sqlite3.Error:
            old_collocations = []
        finally:
            old_conn.close()

    collocations: dict[tuple[str, str], str] = {}
    for word, phrase, translation in old_collocations:
        collocations[(word, phrase)] = translation
    for word, items in COLLOCATIONS.items():
        for phrase, translation in items:
            collocations[(word, phrase)] = translation

    collocations_by_word: dict[str, list[tuple[str, str]]] = {}
    for (word, phrase), translation in collocations.items():
        collocations_by_word.setdefault(word, []).append((phrase, translation))

    temp_path = db_path.with_name(db_path.name + ".update.tmp")
    connection = open_database(db_path, temp_path)
    try:
        connection.executescript(
            """
            DELETE FROM dictionary;
            DELETE FROM word_forms;
            DELETE FROM collocations;
            DELETE FROM meta;
            """
        )

        dictionary_rows = []
        for item in selected:
            word = item["word"]
            phrases = item.get("extra_phrases", "")
            if not phrases:
                phrases = "；".join(
                    f"{phrase}：{translation}"
                    for phrase, translation in collocations_by_word.get(word, [])[:2]
                )
            dictionary_rows.append(
                (
                    word,
                    item["pos"],
                    item["translation"],
                    phrases,
                    item["phonetic"],
                    item["exchange"],
                    item["tag"],
                    item["is_cet4"],
                    item["is_cet6"],
                )
            )
        connection.executemany(
            """
            INSERT OR REPLACE INTO dictionary
                (word, pos, translation, phrases, phonetic, exchange, tag,
                 is_cet4, is_cet6)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            dictionary_rows,
        )

        form_rows: set[tuple[str, str]] = set()
        for item in selected:
            word = item["word"]
            inflections, lemmas = exchange_forms(word, item["exchange"])
            for form in inflections:
                if form and form != word:
                    form_rows.add((form, word))
            for lemma in lemmas:
                if lemma and lemma != word:
                    form_rows.add((word, lemma))
            for form in regular_forms(word):
                if form and form != word:
                    form_rows.add((form, word))
        connection.executemany(
            "INSERT OR IGNORE INTO word_forms(form, lemma) VALUES (?, ?)",
            sorted(form_rows),
        )

        connection.executemany(
            """
            INSERT OR IGNORE INTO collocations(word, phrase, translation)
            VALUES (?, ?, ?)
            """,
            [
                (word, phrase, translation)
                for (word, phrase), translation in collocations.items()
            ],
        )
        connection.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("source", "skywind3000/ECDICT (MIT)"),
                ("tier", "academic-45k"),
                ("built_at", datetime.now(timezone.utc).isoformat()),
                ("word_count", str(len(selected))),
                ("form_count", str(len(form_rows))),
            ],
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check 失败：{integrity}")
        connection.execute("VACUUM")
        connection.commit()
        word_count = connection.execute("SELECT COUNT(*) FROM dictionary").fetchone()[0]
        form_count = connection.execute("SELECT COUNT(*) FROM word_forms").fetchone()[0]
        collocation_count = connection.execute(
            "SELECT COUNT(*) FROM collocations"
        ).fetchone()[0]
    finally:
        connection.close()

    new_size = temp_path.stat().st_size
    if db_path.exists():
        shutil.copy2(db_path, db_path.with_name(db_path.name + ".bak"))
    temp_path.replace(db_path)
    return word_count, form_count, collocation_count, new_size


def main() -> int:
    parser = argparse.ArgumentParser(description="升级 CET4 离线词典到 45k 学术核心词")
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help=f"ECDICT CSV 路径（默认：{DEFAULT_SOURCE}）",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help=f"目标 SQLite 路径（默认：{DEFAULT_DB}）",
    )
    parser.add_argument(
        "--target",
        type=int,
        default=TARGET_WORDS,
        help=f"目标词条数（默认：{TARGET_WORDS}）",
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    try:
        words, forms, collocations, size = build_dictionary(source, db_path, args.target)
    except Exception as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1

    print(f"完成：{db_path}")
    print(f"词条数：{words:,}")
    print(f"词形数：{forms:,}")
    print(f"固定搭配：{collocations:,}")
    print(f"文件大小：{size / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
