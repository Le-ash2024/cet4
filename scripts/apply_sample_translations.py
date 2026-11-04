# -*- coding: utf-8 -*-
"""给自带的示例文章补齐段落、题干和选项中文翻译。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "assets" / "data" / "articles.json"

PARAGRAPH_TRANSLATIONS = [
    "过去，阅读常被视为一种安静的爱好。然而今天，它已成为一种宝贵的习惯，能够同时提高语言能力和常识。许多学生认为他们需要一个特别的环境才能专心读书，但研究表明，简单的日常习惯比完美的环境更重要。",
    "一项针对大学生的研究发现，每天阅读二十分钟的人，比每周一次阅读两小时的人更能记住信息。原因并不难理解。规律的练习能训练注意力，并让大脑更有耐心地处理复杂观点。即使时间很短，只要坚持下去也会有效。",
    "电子屏幕改变了我们的阅读方式。我们能快速找到信息，但常常从一页跳到另一页，没有深入思考。这种习惯会削弱记忆。要从阅读中受益，我们需要放慢速度，把全部注意力放在文本上。一个实用的方法是把手机放到另一个房间，并在身边放一本笔记本。阅读前写下几个问题，也能帮助我们保持专注。",
    "阅读不仅是一项考试技能。它是理解他人和世界的一种方式。广泛阅读的人更可能注意到不同的观点，并清晰地表达自己的想法。阅读的益处不会一夜之间出现，而会随着练习不断增长。如果你想养成持久的习惯，先从小目标开始。每天十页就够了。重要的是让阅读成为日常的一部分，而不是完成一次就忘记的任务。",
]

QUESTION_TRANSLATIONS = [
    {
        "stem_translation": "文中提到的研究说明了什么？",
        "options_translation": {
            "A": "每周一次阅读两小时是最有效的方法。",
            "B": "短时间但规律的阅读习惯效果更好。",
            "C": "学生只有在完美的环境中才能集中注意力。",
            "D": "数字阅读总是会削弱一个人的记忆。",
        },
    },
    {
        "stem_translation": "作者为什么提到把手机放到另一个房间？",
        "options_translation": {
            "A": "为了证明手机对学习毫无用处。",
            "B": "为了给出一种让人全神贯注阅读的实用方法。",
            "C": "为了建议学生多买一些笔记本。",
            "D": "为了解释数字阅读为什么流行。",
        },
    },
    {
        "stem_translation": "这篇文章的主旨是什么？",
        "options_translation": {
            "A": "阅读应仅限于备考。",
            "B": "应完全避免使用电子屏幕。",
            "C": "阅读是一种会随着持续练习而成长的长期习惯。",
            "D": "每天十页的目标太小了。",
        },
    },
]


def main() -> None:
    data = json.loads(ARTICLES.read_text(encoding="utf-8"))
    article = data[0]
    article["passage_translation"] = PARAGRAPH_TRANSLATIONS
    for question, translated in zip(article["questions"], QUESTION_TRANSLATIONS):
        question.update(translated)
    ARTICLES.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"updated {ARTICLES} ({ARTICLES.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
