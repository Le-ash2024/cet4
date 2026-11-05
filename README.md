# 四级阅读刷题助手

Flet + Python 的四级阅读刷题应用，支持 Windows 和 Android。默认离线使用；
文章、题目、解析和翻译可以从内置 assets 读取，也可以通过“同步最新题库”
按钮下载到本地后优先使用。

## 功能

- 按“年份 → 套数 → 题型”浏览文章。
- 正文每个英文单词独立可点击，词卡固定显示三行：单词 + 词性 / 中文释义 /
  带中文意思的常见短语。
- 点击单词时记录并恢复阅读滚动位置，词卡出现/切换不会把页面拉回顶部。
- 每段正文下方有“查看本段翻译”折叠项；解析顶部显示题干和四个选项的中文翻译。
- 点击选项立即判分并展开解析，不设提交按钮。
- 作答记录写入应用私有目录（Android 为 `FLET_APP_STORAGE_DATA`，本地开发为
  `.progress/answers.json`），杀进程重开不会丢；目录显示：
  - ⚪ 未做
  - ✅ 正确完成（全对）
  - ❌ 完成错误（有错题）
- 阅读页顶部（桌面右上角、手机题头信息行）有“重新做题”按钮，清空本篇记录并恢复未作答状态。
- 侧边栏目录和手机横向文章条会保存滚动 offset，切换文章/点词后不会回弹到顶部或最左侧。
- 桌面侧边栏和手机顶栏都有“同步最新题库”入口；更新成功后立即刷新列表，
  失败不会影响已有题库和做题。
- 手机窄屏使用单列阅读布局，桌面使用左侧题库导航。

## 目录结构

```text
cet4_reading_helper/
├─ main.py
├─ extractor.py                  # 真题文本 → JSON 的桌面提取工具
├─ extractor_sample.txt          # 提取工具输入格式示例
├─ batch_docx_import.py          # 批量导入桌面 Word 真题
├─ update_dict.py                # 将词典升级为 45,000 词学术核心词库
├─ requirements-batch.txt        # 批处理工具依赖（python-docx）
├─ pyproject.toml
├─ requirements.txt              # Android/纯运行依赖（flet + requests）
├─ requirements-desktop.txt      # 桌面打包工具
├─ README.md
├─ .github/workflows/build_apk.yml
├─ scripts/
│  ├─ build_ecdict.py            # 从 ECDICT 构建 assets/dict.db
│  └─ apply_sample_translations.py
├─ assets/
│  ├─ icon.png
│  ├─ dict.db                    # 45,000 个核心词 + 155,123 条词形
│  └─ data/
│     └─ articles.json
└─ screenshots/
```

## 运行源码

Windows 桌面环境：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements-desktop.txt
python main.py
```

Android/云端构建只安装纯 Python 运行依赖：

```powershell
python -m pip install -r requirements.txt
```

## 数据格式

`assets/data/articles.json` 是文章数组。除原有字段外，还支持：

```json
{
  "passage_translation": ["第一段中文翻译", "第二段中文翻译"],
  "questions": [
    {
      "stem_translation": "题干中文翻译",
      "options_translation": {
        "A": "A 选项中文",
        "B": "B 选项中文",
        "C": "C 选项中文",
        "D": "D 选项中文"
      }
    }
  ]
}
```

翻译字段可以缺失，缺失时界面显示“暂无翻译”。`passage_translation` 也可以
写成一个用空行分隔的字符串。

## 离线词典

`assets/dict.db` 当前由 `update_dict.py` 从 ECDICT 生成，约 **14.7 MB**：

- `dictionary`：**45,000 个词条**
- `word_forms`：**155,123 条词形**（如 `running → run`、`habits → habit`）
- `collocations`：54 条带中文释义的固定搭配
- 覆盖 CET4/CET6、考研、托福、雅思、GRE 核心词，并按 BNC/COCA 词频补足

重新生成学术词库：

```powershell
python update_dict.py --source build/ecdict.csv --target 45000
```

脚本会保留原表结构，原库自动备份为 `dict.db.bak`。ECDICT 来源：
[skywind3000/ECDICT](https://github.com/skywind3000/ECDICT)（MIT License）。

`scripts/build_ecdict.py` 是早期 7k 词小库的构建脚本，仍可用于生成轻量示例库。

词典查询优先走 ECDICT 词形表，再回退到规则化的复数/过去式/进行时还原。

## 题库热更新

在 [main.py](main.py) 顶部填写题库直链：

```python
UPDATE_URL = "https://raw.githubusercontent.com/你的用户名/你的仓库/main/assets/data/articles.json"
```

要求：

- 地址返回一个 JSON 数组，结构与 `assets/data/articles.json` 完全一致。
- 公开可访问；GitHub Raw 的 URL 可以直接使用。
- App 会先下载到临时文件，用现有 `load_articles()` 完整校验通过后才替换本地题库。
- 下载内容上限 20 MB，超时会提示失败。

启动时的读取优先级：

```text
get_application_documents_directory()/CET4ReadingHelper/articles.json
        ↓（存在且合法）
优先使用本地热更新题库
        ↓（不存在或损坏）
使用内置 assets/data/articles.json
```

如果文档目录不可写，程序会回退到 Flet 注入的 `FLET_APP_STORAGE_DATA`
私有目录；本地开发时回退到项目内 `.local_data/`。

交互：

- 桌面：左侧栏“同步最新题库”。
- 手机：顶栏“同步题库”按钮。
- 同步中显示“正在更新题库...”，按钮暂时禁用。
- 成功：显示“✅ 更新成功，共 N 篇”，并立即重建列表。
- 失败：显示“❌ 更新失败，请检查网络”，当前题库和做题功能保持原样。
- 旧本地缓存会备份为 `articles.json.bak`。
- Flet 的 Android 构建模板默认已声明 `android.permission.INTERNET`，无需手动修改 Manifest。

依赖已在以下文件加入 `requests`：

- `requirements.txt`
- `requirements-desktop.txt`
- `pyproject.toml`

## 批量导入 Word 真题

`batch_docx_import.py` 会递归读取桌面“四级阅读”文件夹下的 `.docx`，复用
`extractor.parse_article_text()` 解析并追加到 `assets/data/articles.json`。

安装依赖：

```powershell
python -m pip install -r requirements-batch.txt
```

运行（默认自动找桌面“四级阅读”）：

```powershell
python batch_docx_import.py
```

或显式指定：

```powershell
python batch_docx_import.py --folder "C:\Users\你\Desktop\四级阅读" --dry-run
python batch_docx_import.py --folder "C:\Users\你\Desktop\四级阅读"
```

处理规则：

- Word 段落之间用空行连接，保持正文和“段落翻译”的分段结构。
- 优先使用文档里的“文章ID”；冲突时尝试文件名；仍冲突则自动加 `_docx` 后缀。
- 正文和题目都相同的文件默认跳过，避免重复运行或重复 Word 文件造成重复题库；
  需要强制导入时可加 `--allow-duplicate-content`。
- 单个 Word 解析失败只打印文件名并跳过，不中断整批。
- 写入前会备份 `articles.json.bak`，再用临时文件原子替换。
- `--dry-run` 只解析不写盘，建议第一次先跑它。

## 真题提取器（Windows 桌面工具）

`extractor.py` 把粘贴的真题文本解析成与现有题库完全一致的 JSON，并追加到
`assets/data/articles.json`。运行：

```powershell
python extractor.py
```

输入格式见 [extractor_sample.txt](extractor_sample.txt)。推荐使用以下标记：

```text
【文章信息】年份 / 月份 / 套数 / 题型 / 文章ID（均可省略）
【文章】正文段落，空行分段
【段落翻译】对应段落翻译，空行分段
【题目】1. 题干
A. ...
B. ...
C. ...
D. ...
答案：B
解析：...
题干翻译：...
选项翻译：
A. ...
B. ...
C. ...
D. ...
```

保存逻辑：

- 先读取现有 JSON，检查旧文章 ID；显式 ID 重复会拒绝写入。
- 未写 ID 时自动生成 `年份_月份_setN_pN`，重复时自动加 `_2` 后缀。
- 保存前重新校验最终 JSON，并以临时文件原子替换。
- 原 `articles.json` 会备份为 `articles.json.bak`。

打包：

```powershell
flet pack extractor.py --name "CET4提取器" --add-data "assets/data:assets/data" --yes
```

生成路径：`dist/CET4提取器.exe`。如果 exe 位于项目的 `dist/` 目录，程序会写回
项目的 `assets/data/articles.json`；如果把 exe 单独复制到别处，第一次运行会在
exe 同级的 `assets/data/` 下建立题库副本。

## Android APK（GitHub Actions）

工作流文件：`.github/workflows/build_apk.yml`。

把项目根目录（本文件夹）推到 GitHub 的 `main` 或 `master` 后会自动构建；
也可以在 **Actions → Build Android APK → Run workflow** 手动触发。

构建完成后在该次运行页面下载：

```text
Artifacts → CET4ReadingHelper-APK → CET4ReadingHelper.apk
```

工作流使用 `arm64-v8a`。如果手机需要 32 位包，把 workflow 中的
`--arch arm64-v8a` 改成 `--arch armeabi-v7a`。

## 本地 APK 构建

安装 JDK 17、Android SDK 36、Build-Tools 36、NDK 28.2 后：

```powershell
flet build apk . `
  --project cet4_reading_helper `
  --product "四级阅读刷题助手" `
  --org com.cet4 `
  --bundle-id com.cet4.readinghelper `
  --artifact CET4ReadingHelper `
  --arch arm64-v8a `
  --python-version 3.12 `
  --skip-flutter-doctor `
  --yes
```

输出目录：`build/apk/`。

## 说明

- Android 生产包中 Flet 会注入 `FLET_ASSETS_DIR`，`main.py` 优先使用它，
  本地开发时回退到 `main.py` 同级的 `assets/`。
- `ensure_sample_data()` 不会改写已有的 `assets/dict.db`，因为 Android assets
  是只读的。
- Flet 1.x 把桌面运行时和 CLI 拆成 extras；Android 的 `requirements.txt` 中
  只有 `flet==1.0.5`，不包含需要本地编译的库。
