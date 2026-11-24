<h1 align="center">KVTree</h1>

<p align="center">把 Markdown 笔记中的大纲转换为 QuickKV 词库。</p>

<p align="center">
  <a href="https://github.com/EllisMorrow/KV-Tree/releases/latest"><img alt="最新发布版本" src="https://img.shields.io/github/v/release/EllisMorrow/KV-Tree"></a>
  <a href="https://github.com/EllisMorrow/KV-Tree/commits/main"><img alt="主分支最近提交" src="https://img.shields.io/github/last-commit/EllisMorrow/KV-Tree/main"></a>
  <a href="https://github.com/EllisMorrow/KV-Tree/releases"><img alt="累计下载量" src="https://img.shields.io/github/downloads/EllisMorrow/KV-Tree/total?label=Downloads&color=brightgreen"></a>
</p>

<p align="center">
  <a href="https://github.com/EllisMorrow/KV-Tree/releases/latest"><strong>下载 Windows 版</strong></a> ·
  <a href="#快速上手">快速上手</a> ·
  <a href="#进阶设置">进阶设置</a> ·
  <a href="#从源码运行">从源码运行</a> ·
  <a href="CHANGELOG.md">更新日志</a>
</p>

<p align="center">
  <a href="assets/workflow.png"><img alt="从 Markdown 大纲提取词条，在 KVTree 中勾选导出，再到 QuickKV 搜索" src="assets/workflow.png" width="820"></a>
</p>

KVTree 适用于在 Obsidian、Logseq 或 Typora 中维护 Markdown 笔记的 [QuickKV](https://github.com/EllisMorrow/QuickKV) 用户。笔记保留原有层级，导出后的每个词条都可以在 QuickKV 中独立搜索。

## 快速上手

### 1. 在笔记中标记词条

在 Markdown 行尾添加 `#KV树-词库名称`。写在行内反引号或围栏代码块里的标签会按正文处理。

| 标签 | 提取范围 |
| --- | --- |
| `#KV树-词库名称` | 当前项 |
| `#KV树-词库名称-父与子` | 当前项和符合规则的子项 |
| `#KV树-词库名称-不包含父` | 符合规则的子项 |

子项使用自己的块级标签时，由它自己的标签决定该分支的归属。

**笔记示例 · `待办事项.md`**

```markdown
- 工作项目 #KV树-我的项目-父与子
    - 撰写报告
    - 回复邮件
- 读书清单 #KV树-我的读书-不包含父
    - 聪明的投资者
    - 漫步华尔街
```

### 2. 预览并导出

1. **启动 KVTree。** 完整解压发布包，然后双击 `KVTree.exe`，保留旁边的 `_internal/`。
2. **添加笔记来源。** 点击「添加文件」或「添加文件夹」。
3. **设置输出目录。** 若希望由 QuickKV 自动载入，选择 QuickKV 配置的词库目录。
4. **勾选导出。** 等待词库预览出现，或点击「全量扫描并重建」。在预览表中勾选要写入磁盘的词库。新发现的词库默认未勾选。

### 3. 查看输出结果

KVTree 将每项写成一行顶格的 `- 词条`，合并多个来源后去重并按文本排序。

**`#KV树-我的项目.md`**

```markdown
- 回复邮件
- 工作项目
- 撰写报告
```

**`#KV树-我的读书.md`**

```markdown
- 漫步华尔街
- 聪明的投资者
```

## 操作演示

**界面参考**

<p align="center">
  <a href="https://github.com/user-attachments/assets/88227df4-11f8-4e5b-bd1c-5e598b8a6b36"><img alt="KVTree 主界面预览" src="https://github.com/user-attachments/assets/88227df4-11f8-4e5b-bd1c-5e598b8a6b36" width="820"></a>
</p>

下面两张图保留了 V0.5–V0.9 的操作演示。当前版本按上面的步骤操作，点击图片可打开原图。

<p align="center">
  <a href="https://github.com/user-attachments/assets/5ca1791c-a537-4a8c-9f71-dd118ff7ff9c"><img alt="KVTree 早期版本操作演示（上）" src="https://github.com/user-attachments/assets/5ca1791c-a537-4a8c-9f71-dd118ff7ff9c" width="390"></a>
  <a href="https://github.com/user-attachments/assets/a65d8d84-97b6-413e-89e9-0883df4dbcb9"><img alt="KVTree 早期版本操作演示（下）" src="https://github.com/user-attachments/assets/a65d8d84-97b6-413e-89e9-0883df4dbcb9" width="390"></a>
</p>

## 进阶设置

| 功能 | 使用方式 |
| --- | --- |
| 后台自动更新 | 勾选「后台自动更新」。程序通常等待约 2 秒合并保存事件，持续变更时批次等待上限约为 10 秒。任务积压和变更量会影响实际完成时间。 |
| 正则过滤 | 在「偏好与高级设置」里配置「排除行」和「排除内容」，可按规则过滤整行或替换行内片段。 |
| 路径排除 | 在「常规偏好 → 排除文件夹 / 文件路径」中每行填写一项，规则应用于扫描、重建和后台更新。 |
| Logseq 属性提取 | 可扫描 Markdown 中的 `键::`、`[[值]]` 和纯文本属性值，并配置排除键。 |
| 来源与外观 | 将鼠标停在「词库数据来源」列可查看完整来源路径。「常规偏好」支持亮色和暗色主题，程序退出时保存窗口位置与大小。 |

默认排除目录为 `.stversions`、`.tine-trash`、`.trash`、`.recycle`、`bak`、`.stfolder` 和 `.obsidian`，这些目录的子内容也会被跳过。

启动时会核对源目录，禁用来源会撤下它贡献的词条。全量重建会清理失去来源的旧词库。输出目录位于笔记源内时，界面会提示生成文件可能被再次扫描。词库、配置和缓存使用同目录临时文件替换，写入或删除词库前会校验输出目录边界。

## 从源码运行

在 Windows 上安装 Python 3.10 或更新版本，然后在项目根目录执行：

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe kv_tree_app.py
```

## 开发与构建

### 运行测试

创建虚拟环境并安装运行依赖后，执行：

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

GitHub Actions 在 Windows 上覆盖 Python 3.10 和 3.13。

### 构建 Windows 可执行文件

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m PyInstaller KVTree.spec --noconfirm
```

构建采用 PyInstaller 文件夹模式（`onedir`）。分发时将整个 `dist/KVTree/` 目录打包，保留 `KVTree.exe` 和 `_internal/` 的相对位置。

### 源码导航

| 位置 | 职责 |
| --- | --- |
| [`kv_tree_app.py`](kv_tree_app.py) | 启动程序，加载配置和缓存 |
| [`src/core/`](src/core/) | 共享状态、后台任务调度和导出 |
| [`src/logic/`](src/logic/) | Markdown 与 Logseq 解析、文件监控、路径过滤和持久化 |
| [`src/ui/`](src/ui/) | Tkinter 主界面、设置控件和系统托盘 |
| [`src/utils/file_utils.py`](src/utils/file_utils.py) | 原子写入文件 |
