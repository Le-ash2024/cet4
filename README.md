# 四级阅读刷题助手

一个只面向 Windows 的 Flet 桌面原型：左侧按“年份 → 套数 → 题型”浏览文章，右侧阅读、点词查义、答题并即时展开解析。运行时完全离线，只读取本地的 JSON 和 SQLite 文件。

## 界面预览

点词后的三行释义卡片（位于文章正下方、题目上方）：

![点词卡片](screenshots/word-card.png)

作答后立即判分并自动把解析滚入视野：

![答题解析](screenshots/answer.png)

## 目录结构

```text
cet4_reading_helper/
├─ main.py              # 完整应用（含自动生成示例数据的逻辑）
├─ requirements.txt
├─ README.md
├─ screenshots/         # 实际运行截图
└─ data/
   ├─ articles.json     # 1 篇示例文章 + 3 道题
   └─ dict.db           # 20 个常用词的离线词典
```

## 运行

在 PowerShell 中进入项目目录：

```powershell
cd cet4_reading_helper
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
python main.py
```

也可以使用 Flet CLI：

```powershell
flet run main.py
```

首次运行如果 `data/articles.json` 或 `data/dict.db` 不存在，程序会自动生成最小示例数据；已有文件不会被覆盖。

> Flet 1.x 首次启动原生桌面窗口时，框架会准备一次本地 Flutter 客户端缓存（不涉及题库数据，也不会调用任何在线 API）。准备好之后即可断网使用；用 `flet pack` 打包后客户端会随 exe 一起分发。

## 替换自己的题库

1. 按 `main.py` 顶部注释中约定的结构（也可以直接参考现有 `data/articles.json`）编辑 `data/articles.json`。
2. 用 SQLite 工具打开 `data/dict.db`，向 `dictionary` 表写入词条：

```sql
INSERT OR REPLACE INTO dictionary(word, pos, translation, phrases)
VALUES ('example', 'n.', '例子；实例', 'for example; set an example');
```

`phrases` 可以用英文分号、中文分号或换行分隔；界面最多展示两个短语，翻译最多展示两个义项。

## 已构建产物

本项目已经完成环境安装和打包，当前可直接使用：

- 单文件版：[dist/CET4ReadingHelper.exe](dist/CET4ReadingHelper.exe)
- 便携目录版（已实际启动验证）：[dist_portable/CET4ReadingHelper/CET4ReadingHelper.exe](dist_portable/CET4ReadingHelper/CET4ReadingHelper.exe)

便携版必须保留同目录下的 `_internal` 文件夹；整个 `CET4ReadingHelper` 文件夹一起复制即可。单文件版首次启动会先解压到系统临时目录，因此启动比便携版稍慢。

## 重新打包

在项目目录执行：

```powershell
flet pack main.py `
  --name CET4ReadingHelper `
  --product-name "四级阅读刷题助手" `
  --file-description "离线四级阅读练习工具" `
  --add-data "data:data"
```

输出：`dist\CET4ReadingHelper.exe`。

如果当前 Flet 版本不接受 `data:data` 的冒号格式，换成 PyInstaller 在 Windows 上的分号格式：

```powershell
flet pack main.py --name CET4ReadingHelper --add-data "data;data"
```

打包后，程序优先读取 **exe 同级** 的 `data/` 目录；不存在时再读取 exe 内置的数据。因此可以直接分发 exe，也可以把 `data/` 文件夹放在 exe 旁边，方便后续替换题库。

## 交互说明

- **点词**：正文每个英文单词都是独立 `TextSpan`，点击后在文章面板正下方显示三行卡片（单词 + 词性 / 中文释义 / 常见短语）。
- **关闭词卡**：点击卡片以外的任意区域即可收起，不跳页、不弹模态框；点击卡片本身不会关闭。
- **答题**：点击 A/B/C/D 后立即显示 `✅ 正确` 或 `❌ 错误`，展开本题解析并自动滚动到判分/解析区域；每道题只记录第一次选择。
- **导航**：点击左侧文章行，右侧切换到对应阅读页。

## 常见问题

- **提示缺少 `flet_desktop` / 没有 `flet` 命令**：你的 Flet 是 1.x，需要安装 extras。执行 `python -m pip install -r requirements.txt`，不要只执行 `pip install flet`。
- **打包后提示找不到数据**：确认执行了 `--add-data`；也可以把 `data/` 文件夹手工放到 exe 同级目录后重启。
- **中文显示异常**：程序主题默认使用微软雅黑；移除系统字体或使用精简版 Windows 时，可在 `configure_page()` 中把 `font_family` 改成其他中文字体。
