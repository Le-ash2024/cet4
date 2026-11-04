# -*- coding: utf-8 -*-
"""
Build an offline CET-4/CET-6 dictionary from ECDICT.

Usage:
    python scripts/build_ecdict.py
    python scripts/build_ecdict.py --source path/to/ecdict.csv
    python scripts/build_ecdict.py --output assets/dict.db

The script filters ECDICT entries tagged `cet4` or `cet6`, writes a compact
SQLite database and adds an inflection table generated from ECDICT's
`exchange` field plus regular English morphology rules.

ECDICT: https://github.com/skywind3000/ECDICT (MIT License)
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "assets" / "dict.db"
DEFAULT_CACHE = ROOT / "build" / "ecdict.csv"
MIN_ECDICT_BYTES = 50_000_000
DOWNLOAD_URLS = [
    "https://ghproxy.net/https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv",
    "https://cdn.jsdelivr.net/gh/skywind3000/ECDICT@master/ecdict.csv",
    "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv",
]

POS_RE = re.compile(r"^([a-zA-Z]+\.)\s*")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
CLEAN_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'’\- ]*$")

# 高频四六级固定搭配；字段为 (英文短语, 中文意思)。
COLLOCATIONS: dict[str, list[tuple[str, str]]] = {
    "account": [("account for", "解释；占……比例")],
    "adapt": [("adapt to", "适应")],
    "apply": [("apply for", "申请"), ("apply to", "适用于")],
    "attention": [("pay attention to", "注意；关注")],
    "aware": [("be aware of", "意识到；知道")],
    "benefit": [("benefit from", "从……中受益")],
    "break": [("break down", "出故障；分解"), ("break through", "突破")],
    "bring": [("bring about", "带来；引起"), ("bring up", "抚养；提出")],
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
    "give": [("give up", "放弃"), ("give in", "屈服；让步")],
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
    "keep": [("keep up with", "跟上"), ("keep in mind", "牢记")],
    "look": [("look forward to", "期待"), ("look into", "调查")],
    "put": [("put up with", "忍受"), ("put emphasis on", "强调")],
    "regard": [("regard as", "把……视为")],
    "responsible": [("be responsible for", "对……负责")],
    "instead": [("instead of", "代替；而不是")],
    "due": [("due to", "由于")],
    "despite": [("despite of", "尽管（不推荐；常用 despite）")],
    "regardless": [("regardless of", "不管；无论")],
    "deal": [("deal with", "处理；应对")],
    "catch": [("catch up with", "赶上")],
    "get used": [("be used to", "习惯于")],
    "likely": [("be likely to", "可能做某事")],
}

# ECDICT 中部分基础功能词不一定带 CET 标签，但示例文章需要可查。
EXTRA_ENTRIES: list[tuple[str, str, str, str]] = [
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


def download_ecdict(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size >= MIN_ECDICT_BYTES:
        print(f"Using cached ECDICT: {dest} ({dest.stat().st_size:,} bytes)")
        return dest
    if dest.exists():
        print(f"Ignoring incomplete ECDICT cache ({dest.stat().st_size:,} bytes)")
        dest.unlink()

    errors: list[str] = []
    for url in DOWNLOAD_URLS:
        try:
            print(f"Downloading {url}")
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(request, timeout=60) as response, dest.open(
                "wb"
            ) as output:
                total = int(response.headers.get("Content-Length") or 0)
                downloaded = 0
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    downloaded += len(chunk)
                    if downloaded % (10 * 1024 * 1024) < 1024 * 1024:
                        if total:
                            print(
                                f"  {downloaded / 1024 / 1024:.0f} / "
                                f"{total / 1024 / 1024:.0f} MB"
                            )
                        else:
                            print(f"  {downloaded / 1024 / 1024:.0f} MB")
            if dest.stat().st_size >= MIN_ECDICT_BYTES:
                print(f"Downloaded ECDICT: {dest} ({dest.stat().st_size:,} bytes)")
                return dest
            print(f"  incomplete download: {dest.stat().st_size:,} bytes")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            print(f"  failed: {exc}")

    raise RuntimeError(
        "Could not download ECDICT. Put ecdict.csv locally and pass "
        "--source path/to/ecdict.csv.\n" + "\n".join(errors)
    )


def clean_translation(raw: str) -> tuple[str, str]:
    text = (raw or "").replace("\\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return "—", ""

    pos_values: list[str] = []
    meanings: list[str] = []
    for line in lines:
        match = POS_RE.match(line)
        if match:
            pos = match.group(1).lower()
            if pos not in pos_values:
                pos_values.append(pos)
            line = line[match.end() :].strip()
        for piece in re.split(r"[,，;；/、]", line):
            piece = piece.strip(" .;；")
            if (
                len(meanings) < 2
                and piece
                and CJK_RE.search(piece)
                and piece not in meanings
            ):
                meanings.append(piece)

    if not meanings:
        return "/".join(pos_values[:2]) or "—", ""
    return "/".join(pos_values[:2]) or "—", "；".join(meanings[:2])


def regular_forms(word: str) -> list[str]:
    forms: set[str] = set()
    lower = word.lower()
    if not lower.isalpha() or len(lower) < 2:
        return []
    if lower.endswith("y") and len(lower) > 2 and lower[-2] not in "aeiou":
        forms.add(lower[:-1] + "ies")
        forms.add(lower[:-1] + "ied")
    else:
        forms.add(lower + "s")
        forms.add(lower + "ed")
    if lower.endswith("e"):
        forms.add(lower + "d")
        forms.add(lower[:-1] + "ing")
    else:
        forms.add(lower + "ing")
    if lower.endswith(("s", "x", "ch", "sh")):
        forms.add(lower + "es")
    return sorted(forms)


def exchange_forms(word: str, exchange: str) -> tuple[list[str], list[str]]:
    """Return (inflected forms of word, lemmas that word points to)."""
    inflections: list[str] = []
    lemmas: list[str] = []
    for item in (exchange or "").split("/"):
        if ":" not in item:
            continue
        kind, value = item.split(":", 1)
        value = value.strip().lower()
        if not value:
            continue
        if kind == "0":
            lemmas.append(value)
        else:
            inflections.append(value)
    return inflections, lemmas


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE dictionary (
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
        CREATE TABLE word_forms (
            form  TEXT NOT NULL,
            lemma TEXT NOT NULL,
            PRIMARY KEY (form, lemma)
        );
        CREATE TABLE collocations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            word        TEXT NOT NULL,
            phrase      TEXT NOT NULL,
            translation TEXT NOT NULL,
            UNIQUE(word, phrase)
        );
        CREATE TABLE meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def build(source: Path, output: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"ECDICT source not found: {source}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(output.name + ".tmp")
    if temp_output.exists():
        temp_output.unlink()

    connection = sqlite3.connect(str(temp_output))
    try:
        init_db(connection)
        total_rows = 0
        cet4_count = 0
        cet6_count = 0

        with source.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                total_rows += 1
                word = (row.get("word") or "").strip().replace("’", "'")
                lower = word.lower()
                tag = (row.get("tag") or "").lower()
                tag_tokens = set(tag.split())
                # gk/zk are high-school/middle-school basics (e.g. "run" only
                # carries "gk" in ECDICT) and are essential for reading.
                if (
                    not word
                    or not CLEAN_WORD_RE.match(word)
                    or not (tag_tokens & {"cet4", "cet6", "gk", "zk"})
                ):
                    continue

                pos, translation = clean_translation(row.get("translation") or "")
                if not translation:
                    continue

                is_cet4 = int("cet4" in tag)
                is_cet6 = int("cet6" in tag)
                cet4_count += is_cet4
                cet6_count += is_cet6
                exchange = (row.get("exchange") or "").strip()
                connection.execute(
                    """
                    INSERT OR REPLACE INTO dictionary
                        (word, pos, translation, phrases, phonetic, exchange, tag,
                         is_cet4, is_cet6)
                    VALUES (?, ?, ?, '', ?, ?, ?, ?, ?)
                    """,
                    (
                        lower,
                        pos,
                        translation,
                        (row.get("phonetic") or "").strip(),
                        exchange,
                        tag,
                        is_cet4,
                        is_cet6,
                    ),
                )

                inflections, lemmas = exchange_forms(lower, exchange)
                for form in inflections:
                    if form and form != lower:
                        connection.execute(
                            "INSERT OR IGNORE INTO word_forms(form, lemma) VALUES (?, ?)",
                            (form, lower),
                        )
                for lemma in lemmas:
                    if lemma and lemma != lower:
                        connection.execute(
                            "INSERT OR IGNORE INTO word_forms(form, lemma) VALUES (?, ?)",
                            (lower, lemma),
                        )
                for form in regular_forms(lower):
                    connection.execute(
                        "INSERT OR IGNORE INTO word_forms(form, lemma) VALUES (?, ?)",
                        (form, lower),
                    )

                if total_rows % 10000 == 0:
                    print(f"  scanned {total_rows:,} rows, kept {cet4_count + cet6_count:,}")

        if cet4_count + cet6_count < 1000:
            raise RuntimeError(
                "ECDICT source looks incomplete: fewer than 1000 CET words "
                f"were found (source rows: {total_rows}). Re-download ecdict.csv "
                "or pass a complete --source file."
            )

        # Ensure the demo article's function words are always available.
        for word, pos, translation, phrases in EXTRA_ENTRIES:
            connection.execute(
                """
                INSERT OR REPLACE INTO dictionary
                    (word, pos, translation, phrases, phonetic, exchange, tag,
                     is_cet4, is_cet6)
                VALUES (?, ?, ?, ?, '', '', 'extra', 0, 0)
                """,
                (word, pos, translation, phrases),
            )

        for word, entries in COLLOCATIONS.items():
            for phrase, translation in entries:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO collocations(word, phrase, translation)
                    VALUES (?, ?, ?)
                    """,
                    (word, phrase, translation),
                )

        connection.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            ("source", "skywind3000/ECDICT (MIT)"),
        )
        connection.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            ("built_at", datetime.now(timezone.utc).isoformat()),
        )
        connection.execute(
            "CREATE INDEX idx_word_forms_form ON word_forms(form)"
        )
        connection.execute(
            "CREATE INDEX idx_collocations_word ON collocations(word)"
        )
        connection.commit()
        word_count = connection.execute("SELECT COUNT(*) FROM dictionary").fetchone()[0]
        form_count = connection.execute("SELECT COUNT(*) FROM word_forms").fetchone()[0]
        connection.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)", ("word_count", str(word_count))
        )
        connection.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)", ("form_count", str(form_count))
        )
        connection.commit()
    finally:
        connection.close()

    temp_output.replace(output)
    print(
        f"Built {output} ({output.stat().st_size / 1024 / 1024:.2f} MB): "
        f"{word_count:,} words, {form_count:,} forms, "
        f"{total_rows:,} source rows"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        help="Path to an existing ecdict.csv (skips download)",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help=f"Download cache path (default: {DEFAULT_CACHE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"SQLite output path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    try:
        source = args.source or download_ecdict(args.cache)
        build(source, args.output)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
