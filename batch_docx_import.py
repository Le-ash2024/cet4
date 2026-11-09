# -*- coding: utf-8 -*-
"""
批量导入桌面 Word 真题到 assets/data/articles.json
=================================================

用法：
    python batch_docx_import.py
    python batch_docx_import.py --folder "C:\\Users\\Yule\\Desktop\\四级阅读"
    python batch_docx_import.py --folder "..." --data "assets/data/articles.json"
    python batch_docx_import.py --dry-run

默认读取桌面上的“四级阅读”文件夹，递归查找所有 .docx，并用
extractor.parse_article_text() 复用现有的文本解析逻辑。

脚本会：
- 缺少 python-docx 时尝试自动安装；
- 跳过 ~$ 开头的 Word 临时文件；
- 每个 Word 文件的段落用空行连接，保留正文/翻译的自然分段；
- 优先沿用文档里的“文章ID”，冲突时尝试用文件名；
- 内容和题目都相同则跳过（可用 --allow-duplicate-content 强制导入）；
- ID 仍冲突但内容不同，自动加 _docx 后缀；
- 文件解析失败时只跳过该文件，不影响其他文件；
- 写入前备份原 articles.json，再用临时文件原子替换。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = PROJECT_DIR / "assets" / "data" / "articles.json"
DEFAULT_FOLDER_CANDIDATES = [
    Path.home() / "Desktop" / "四级阅读",
    Path.home() / "OneDrive" / "Desktop" / "四级阅读",
    PROJECT_DIR / "四级阅读",
]
DOCX_MODULE: Any = None


def ensure_python_docx() -> Any:
    """导入 python-docx；缺失时尝试安装，失败则给出明确命令。"""
    global DOCX_MODULE
    if DOCX_MODULE is not None:
        return DOCX_MODULE

    try:
        import docx  # type: ignore

        DOCX_MODULE = docx
        return docx
    except ImportError:
        print("[准备] 未检测到 python-docx，正在尝试安装 python-docx==1.2.0 ...")

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "python-docx==1.2.0"]
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[错误] 自动安装 python-docx 失败：{exc}")
        print("请手动执行：")
        print(f'  "{sys.executable}" -m pip install python-docx==1.2.0')
        raise SystemExit(1) from exc

    try:
        import docx  # type: ignore

        DOCX_MODULE = docx
        return docx
    except ImportError as exc:
        print("[错误] python-docx 安装后仍无法导入。")
        print(f'请手动执行："{sys.executable}" -m pip install python-docx==1.2.0')
        raise SystemExit(1) from exc


def normalize_cell(text: str) -> str:
    return (
        (text or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .strip()
    )


def extract_docx_text(docx: Any, path: Path) -> str:
    """
    读取 Word 段落。

    Word 里每个正文/翻译段落通常是独立的 w:p；这里用空行连接，复用
    extractor 中按空行分段的逻辑。单段落内部的换行会原样保留。
    """
    document = docx.Document(str(path))
    parts: list[str] = []
    for paragraph in document.paragraphs:
        text = normalize_cell(paragraph.text)
        if text:
            parts.append(text)

    # 如果有人在 Word 里用表格排版，也尽量把表格文字带上；正常题库
    # 文档不会走这里，因此不会影响常规段落顺序。
    for table in document.tables:
        for row in table.rows:
            cells = [normalize_cell(cell.text) for cell in row.cells]
            cells = [cell for cell in cells if cell]
            if cells:
                parts.append("  ".join(cells))

    return "\n\n".join(parts)


OPTION_QUOTE_RE = re.compile(
    r"（(?:(?P<kind>选项)\s*|(?P<letter>[A-D])\s*项?\s*[:：]?\s*)"
    r"(?P<snippet>[^（）]+?)(?:…|\.\.\.)）"
)


def _normalize_quote(text: str) -> str:
    return re.sub(r"[\s\"“”‘’.,;:!?…]+", "", (text or "").lower())


def expand_option_quotes(explanation: str, options: dict[str, Any]) -> str:
    """
    Word 文档里常把解析中的选项引文缩写成
    “（选项 focus their attention o…）”。
    这里用题干 A-D 的完整选项文本还原引文，避免用户看到省略号。
    """
    option_texts = {
        letter: str(options.get(letter, "")).strip()
        for letter in ("A", "B", "C", "D")
    }

    def replace(match: re.Match[str]) -> str:
        snippet = re.sub(
            r"^[说：:，,\s]+", "", match.group("snippet").strip()
        )
        if not snippet:
            return match.group(0)

        letter = (match.group("letter") or "").upper()
        candidates: list[tuple[str, str]] = []
        if letter:
            full = option_texts.get(letter, "")
            if full and _normalize_quote(snippet) in _normalize_quote(full):
                candidates = [(letter, full)]
        if not candidates:
            snippet_norm = _normalize_quote(snippet)
            candidates = [
                (key, text)
                for key, text in option_texts.items()
                if text and snippet_norm and snippet_norm in _normalize_quote(text)
            ]
        if len(candidates) != 1:
            return match.group(0)

        chosen_letter, full_text = candidates[0]
        if match.group("kind") == "选项":
            return f"（选项 {full_text}）"
        if match.group("letter"):
            return f"（{chosen_letter}项 {full_text}）"
        return f"（选项 {full_text}）"

    return OPTION_QUOTE_RE.sub(replace, explanation or "")


def expand_article_option_quotes(article: dict[str, Any]) -> int:
    """展开一篇文章里所有题目解析的选项缩写，返回修改的题目数。"""
    changed = 0
    for question in article.get("questions", []):
        if not isinstance(question, dict):
            continue
        explanation = question.get("explanation", "")
        options = question.get("options")
        if isinstance(explanation, str) and isinstance(options, dict):
            expanded = expand_option_quotes(explanation, options)
            if expanded != explanation:
                question["explanation"] = expanded
                changed += 1
    return changed


def load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"无法读取题库文件：{path}\n{exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"题库文件不是合法 JSON：{path}\n"
            f"第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}"
        ) from exc
    if not isinstance(data, list):
        raise RuntimeError("articles.json 顶层必须是 JSON 数组。")
    return data


def safe_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip())
    return value.strip("._-")


def article_fingerprint(article: dict[str, Any]) -> str:
    """用于判断某篇 Word 是否已经导入过，避免重复运行产生重复文章。"""
    questions: list[dict[str, Any]] = []
    raw_questions = article.get("questions")
    if isinstance(raw_questions, list):
        for question in raw_questions:
            if not isinstance(question, dict):
                continue
            options = question.get("options")
            questions.append(
                {
                    "stem": re.sub(r"\s+", " ", str(question.get("stem", ""))).strip().lower(),
                    "options": {
                        letter: re.sub(
                            r"\s+", " ", str((options or {}).get(letter, ""))
                        ).strip().lower()
                        for letter in ("A", "B", "C", "D")
                    },
                    "answer": str(question.get("answer", "")).strip().upper(),
                }
            )
    payload = {
        "passage": re.sub(
            r"\s+", " ", str(article.get("passage", ""))
        ).strip().lower(),
        "questions": questions,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def choose_unique_id(
    article: dict[str, Any],
    source_path: Path,
    used_ids: set[str],
) -> tuple[str, str]:
    """
    优先使用文档里的 ID；冲突时尝试文件名；仍冲突时生成 _docx 后缀。
    内容完全相同的文章会在调用前被跳过，因此正常重复运行不会追加副本。
    """
    candidates: list[str] = []
    original = safe_id(str(article.get("id", "")))
    from_filename = safe_id(source_path.stem)
    if original:
        candidates.append(original)
    if from_filename and from_filename not in candidates:
        candidates.append(from_filename)

    for candidate in candidates:
        if candidate and candidate not in used_ids:
            if candidate != original:
                return candidate, f"ID {original or '（空）'} 已存在，改用 {candidate}"
            return candidate, ""

    base = from_filename or original or "docx_article"
    base = re.sub(r"_docx\d*$", "", base, flags=re.IGNORECASE)
    candidate = f"{base}_docx"
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}_docx{suffix}"
        suffix += 1
    return candidate, f"ID {original or from_filename or '（空）'} 已存在，改用 {candidate}"


def save_articles(path: Path, articles: list[dict[str, Any]]) -> None:
    serialized = json.dumps(articles, ensure_ascii=False, indent=2) + "\n"
    # 写盘前先重新验证 JSON 可解析。
    json.loads(serialized)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_text(serialized, encoding="utf-8")
        if path.exists():
            try:
                shutil.copy2(path, path.with_name(path.name + ".bak"))
            except OSError:
                pass
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def find_folder(explicit: str | None) -> Path:
    if explicit:
        folder = Path(explicit).expanduser()
        if not folder.exists():
            raise FileNotFoundError(f"文件夹不存在：{folder}")
        return folder
    for candidate in DEFAULT_FOLDER_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "没有找到默认文件夹，请用 --folder 指定，例如：\n"
        '  python batch_docx_import.py --folder "C:\\Users\\你\\Desktop\\四级阅读"'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="批量导入 CET4 Word 题库")
    parser.add_argument(
        "--folder",
        help="包含 .docx 的文件夹；默认自动查找桌面“四级阅读”",
    )
    parser.add_argument(
        "--data",
        default=str(DEFAULT_DATA),
        help=f"articles.json 路径（默认：{DEFAULT_DATA}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只解析并打印结果，不写入 articles.json",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="只处理前 N 个文件（调试用，0 表示全部）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="失败时打印完整 traceback",
    )
    parser.add_argument(
        "--allow-duplicate-content",
        action="store_true",
        help="即使正文/题目完全相同也导入（默认会跳过重复内容）",
    )
    args = parser.parse_args()

    docx = ensure_python_docx()
    # 延迟导入，确保 python-docx 的安装提示先执行。
    try:
        import extractor  # type: ignore
    except Exception as exc:
        print(f"[错误] 无法导入 extractor.py：{exc}")
        print("请确认在项目目录运行，并使用项目的 .venv Python。")
        return 1

    try:
        folder = find_folder(args.folder)
        data_path = Path(args.data).expanduser().resolve()
        existing = load_existing(data_path)
    except Exception as exc:
        print(f"[错误] {exc}")
        return 1

    files = sorted(folder.rglob("*.docx"))
    files = [path for path in files if not path.name.startswith("~$")]
    if args.limit and args.limit > 0:
        files = files[: args.limit]
    if not files:
        print(f"[错误] 文件夹里没有找到 .docx：{folder}")
        return 1

    used_ids = {
        str(item.get("id"))
        for item in existing
        if isinstance(item, dict) and item.get("id")
    }
    existing_fingerprints = {
        article_fingerprint(item)
        for item in existing
        if isinstance(item, dict) and item.get("passage")
    }
    imported = 0
    skipped_duplicate = 0
    failed: list[tuple[Path, str]] = []

    print(f"题库文件：{data_path}")
    print(f"Word 文件夹：{folder}")
    print(f"发现 {len(files)} 个 .docx，现有 {len(existing)} 篇文章")
    if args.dry_run:
        print("当前为 --dry-run，只解析，不写入。")
    print("-" * 70)

    for index, path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] {path.name}")
        try:
            text = extract_docx_text(docx, path)
            if not text.strip():
                raise ValueError("Word 文档没有可读取的段落文字。")
            article = extractor.parse_article_text(text)
            expand_article_option_quotes(article)
            fingerprint = article_fingerprint(article)
            if (
                not args.allow_duplicate_content
                and fingerprint in existing_fingerprints
            ):
                skipped_duplicate += 1
                print("  [跳过] 题库中已有相同正文/题目，避免重复导入。")
                continue
            new_id, note = choose_unique_id(article, path, used_ids)
            article["id"] = new_id
            existing.append(article)
            used_ids.add(new_id)
            existing_fingerprints.add(fingerprint)
            imported += 1
            print(
                f"  [成功] {new_id} | {len(article['questions'])} 题"
                + (f" | {note}" if note else "")
            )
        except Exception as exc:
            failed.append((path, str(exc)))
            print(f"  [跳过] 无法解析：{exc}")
            if args.verbose:
                traceback.print_exc(limit=3)

    print("-" * 70)
    print(
        f"完成：成功 {imported} 篇，因内容重复跳过 {skipped_duplicate} 篇，"
        f"解析失败 {len(failed)} 篇。"
    )
    if failed:
        print("失败文件：")
        for path, _ in failed:
            print(f"  - {path.name}")

    if args.dry_run:
        print("dry-run 模式未写入题库。")
        return 0

    if imported == 0:
        print("没有新文章需要写入。")
        return 0 if skipped_duplicate else 1

    try:
        save_articles(data_path, existing)
    except Exception as exc:
        print(f"[错误] 写入题库失败：{exc}")
        return 1

    print(f"已写入：{data_path}")
    print(f"当前题库共 {len(existing)} 篇。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
