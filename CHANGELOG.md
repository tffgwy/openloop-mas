# Changelog

All notable changes to OpenLoop MAS are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

# Changelog

All notable changes to OpenLoop MAS are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.73] - 2026-11-17

### agent 能提草稿，但改不改由人说了算

v0.7.72 把「改动先进草稿」落成了**引擎级**规则：循环改一个已存在的文件时，
先记一份草稿，人批准了才写回。**但那条规则只管住了循环自己** ——
一个会说话的 agent 拿着 MCP 工具，仍然可以直接改你的文件，
根本用不到草稿这一层。

这一版把同一套规则接到 **MCP** 上，并且分成两半：
**提议**和**批准**不是同一个动作。

- **新工具 `mas_drafts`** —— 列出待确认的草稿，或看某一个草稿的 diff。
- **新工具 `mas_draft_stage`** —— agent 经 MCP **提**一份改动。
  原文件**一个字节都不动**，草稿带 diff 和理由。
- **新工具 `mas_draft_resolve`** —— 批准或驳回。**批准要人类令牌**
  （`mas drafts token`）：**agent 提的草稿，agent 自己批不了**。
  令牌不对、空令牌、没令牌，一律拒绝，而且**原文件不动**。
- **新工具 `mas_files` / `mas_preview`** —— agent 也能索引工作区、预览文件，
  **提改动之前先看清楚要改的是什么**。
  两个工具**认工作区**：相对路径按给定的工作区解析，
  不再去读进程当前目录（这个缺口是 E2E 真跑出来的）。
- **人类令牌** —— `<state_dir>/human.token`，`mas drafts token` 取，
  权限位设成 `0600`（Windows 上靠文件夹保护）。
  令牌的作用**不是保密，是分权**：提议的人不确认自己提的东西。
- **共享的草稿查找** —— `mas drafts`（CLI）和 MCP 现在走同一套
  `mas/drafts.py` 查找逻辑，不会各说各话。
- **第 16 项门禁 `mcp_draft_approval`** —— 前面十五项门禁看**引擎**、
  **CLI**、**报告**、**MCP 握手**，**没有任何门禁看「agent 能不能自己批自己的草稿」**。
  这个缝是用**反证**查出来的：MCP 草稿路径的**十二个变异全部被捕获**，
  其中两个就是**路径解析被改坏**（供应链只测绝对路径，没测相对路径）。
- **门禁读两遍** —— 这道门禁的判定被**独立实现了两遍**，
  两边必须一致才算过。改坏判定函数本身，会被对照的那一份当场抓住 ——
  **一个只能骗过产品、骗不过自己的门禁，不是门禁**。
  写成 `True` 的语义等价变异当然抓不住（它本来就是对的），
  但**写成错误判定的变异全部被捕获**。

## [0.7.72] - 2026-11-17

### 改动先进草稿，人确认后才写回原文件

前面每一版都在**读**和**写**上使劲，但**没有一版问过「谁来批准这次改动」**。

一次运行想改一个**已经存在**的文件时，引擎就真的把它覆盖了 ——
**你原来写的东西没了，而你甚至没看见它被改成了什么。**
最危险的时刻不是循环跑不动，而是它**跑得太顺**。

MOKI（DeepSeek Harness 的本地资源管理器插件）给这条规则起了个名字：
**修改先进草稿，用户审核确认后再保存回原文件。**
v0.7.71 补的是这条规则的**读的那一半**（`mas files` / `mas preview`），
这一版补的是**写的那一半**。

- **第 15 项门禁 `draft_approval`** —— 前面十四项门禁看**读**、**写**、
  **索引**，**没有任何门禁看「改动有没有经过人」**。
  这个缝是用**反证**查出来的：草稿路径的**八个变异全部被捕获**。

- **新模块 `mas/drafts.py`** —— 只用标准库：
  - `stage_draft()` —— 把改动**记成草稿**，**原文件不动**；
    对**不存在的文件**拒绝（那是创建，不是编辑）；
  - `approve_draft()` —— **唯一**把草稿写回原文件的地方；
  - `reject_draft()` —— 丢弃草稿，**原文件一个字都不改**；
  - `list_drafts()` / `get_draft()` —— 报告在等什么；
  - `draft_overlay()` —— 把待批草稿变成**机检 overlay**，让机检看到提案字节。

- **配置项 `edit_policy`** —— 默认 `"draft"`，改成 `"overwrite"`
  就回到旧行为。**新建文件照常直接写** —— 一个还不存在的文件，
  没有东西可以毁掉，草稿规则不该在那里添乱。

- **草稿住在工作区里**（`.mas-drafts/`）—— 和它改的那个文件在一起。
  住在中心目录的草稿，会在文件夹被复制时一起丢失；
  丢掉的草稿，是一份**永远没法被人审阅**的改动。
  字节单独存（`.data`）—— diff 是**改动的报告**，不是**改动本身**。

- **机检读的是草稿里的新字节** —— 这是这一层最容易做错的地方。
  如果机检读磁盘上的旧文件，循环就会陷入死结：
  提改动 → 读旧文件 → 判不通过 → 再提改动 → 还是读旧文件……**永远闭不上**。
  所以机检读的是 overlay，循环**能用自己的提案闭合**：
  **批准与否是关于那个文件的，不是关于这次判定的。**

- **新命令 `mas drafts [list|show|approve|reject]`** ——
  默认工作区指向 `<state_dir>/artifacts/`，会**往下走一层**找到运行工作区里的草稿，
  所以人**不用知道是哪个运行**留下的草稿。四个动作都有 `--json`。

- **这一版修掉的真 bug**（都是先被反证抓出来的）：
  - 引擎里 `_apply_writes` 被**重复插入了六份** —— 去重时把
    「对抗评审」的段头一起删掉了，**差点把一段评审代码吞掉**；
  - `mas/drafts.py` 的模块文档字符串**被 PowerShell here-string 截断**，
    文件末尾的 `"""` 变成了 `'''` —— 模块**根本 import 不了**；
    **门禁当场抓住了它**（这正是第 15 项门禁存在的意义）；
  - 门禁最初**直接读草稿仓库**，绕过了引擎 —— 于是「机检看不到草稿字节」
    这个变异**溜过去了**。现在门禁**同时读引擎的 overlay 和仓库的 overlay**，
    引擎那条路坏了它就红。

- **和 MOKI 的关系**：MOKI 在**交互层**做这条规则（`Alt+D` 唤起 AI、
  改完先进草稿），本项目在**引擎层**做同一条规则 ——
  不管改动是模型提的、agent 提的、还是脚本提的，
  只要它想覆盖一个已有的文件，**它就必须先变成草稿**。
  详见 [`docs/drafts.md`](docs/drafts.md)。
## [0.7.71] - 2026-11-16

### 循环要能说出自己产出了什么

前面每一版都在**读**和**写**上使劲，但**没有一版回答过最朴素的那个问题**：
**「这个循环到底产出了什么？」**

答案过去是散的：`mas artifacts` 只列**交付物**，`mas status` 只讲**机器**，
一个既不是交付物、也没有任何验收标准提到的文件 —— 一份佐证 PDF、
一个字体、一个 3D 模型 —— **根本没有地方列**。

- **第 14 项门禁 `resource_index`** —— 第 12 项看**读**，
  第 13 项看**写**，**没有任何门禁看「产出物的清单」**。
  这个缝是用**反证**查出来的：索引路径的**七个变异全部被捕获**。

- **新模块 `mas/formats.py`** —— 只用标准库，**只读、不执行任何东西**：
  - `font_info()` —— TTF / OTF / TTC / WOFF / WOFF2：解析 sfnt 表目录，
    从 `name` 表取家族与子家族，从 `maxp` 取字形数；
  - `image_info()` —— JPEG（SOF 标记）、PNG / GIF / BMP、
    WEBP（VP8 / VP8L / VP8X）、TIFF（IFD）：像素宽高；
  - `media_info()` —— WAV（fmt / data 块、时长）、
    MP4 / MOV（ISO box 树、ftyp 品牌、mvhd 时长、轨道数）、
    FLAC（STREAMINFO）、OGG、MP3、Matroska；
  - `model_info()` —— GLB（chunk 解析）、glTF（JSON）、
    OBJ（顶点 / 面计数）、STL（二进制 / ASCII 三角面）、
    FBX（版本）、PLY（element 计数）、3MF（包内 `.model` 数）。
  **截断的文件和撒谎的文件返回空结果** —— 只报文件头能证明的事实。

- **新模块 `mas/resources.py`** —— 一个文件夹就是一份索引：
  - `describe_folder(root)` —— 每个文件一行，含 `relative` / `format` /
    `bytes` / `readable` / `reason`；
  - `format_summary(index)` —— 按格式分组；
  - `facts_for(row)` —— 渲染可读事实串；
  - `preview_file(path)` —— 预览一个文件；只对**有正文的格式**抽正文。

- **两个新命令**：
  - `mas files [路径]` —— 索引一个文件夹；不给路径就索引
    `<state_dir>/artifacts/*` 下所有运行工作区；
  - `mas preview <文件> [--max-chars N]` —— 预览一个文件；
    文件不存在或传了目录都**退出码 2**。

- **格式判定扩了 12 种**：`font` / `audio` / `video` / `model` / `archive`
  加进 `KNOWN_FORMATS`；magic bytes 新增字体、glTF / FBX / PLY、
  7z / Rar；RIFF 分支细分出 WEBP / WAVE / AVI。

- **这一版修掉的真 bug**（都是先被真实样本抓出来的）：
  - GLB 的容器头是**小端**，之前按大端读，版本号读成 `131072.0`；
  - GLB 的 chunk 从**第 12 字节**开始，不是第 20 字节；
  - `.gltf` 是**纯 JSON**，头两个字节是 `{`，只能靠扩展名认 ——
    之前它一个事实都读不出来；
  - `.zip` 被归档分支抢走，报 `archive` 而不是 `zip`，成员数也就丢了；
  - PLY 的 `element vertex 3` 存成了 `vertex: 3`，字段名跟事实对不上；
  - STL 的 ASCII 形态被当成二进制，header 不够 84 字节就什么也不报；
  - PNG / GIF / BMP 的像素尺寸在 `mas/formats.py` 里**读不出来**
    （只读 JPEG / WEBP / TIFF）。

- **和 MOKI 的关系**：MOKI 给 DeepSeek Harness 加了**本地资源管理器**
  （统一管理 + 多格式预览 + `Alt+D` 唤起 AI 协同编辑，
  **修改先进草稿、人工确认后才写回**）。这一版补的是**读的那一半**：
  先把「有哪些文件、是什么、能读出么」变成一条命令。
  详见 [`docs/resources.md`](docs/resources.md)。

## [0.7.70] - 2026-11-16

### 机检要能读进文件里，而不是只认字节

前面所有文件级机检搜的都是**文件的字节**。这对「文件里有一行这句话」够用，
但对**每一种正文不是明文的格式**都是瞎的：PDF 把文字放在**压缩流**里，
`.docx` 本身就是个 **zip 包**，图片里根本没有文字。

于是**交付物是 PDF 的任务，机检根本判不了**。标准落回模型手里，
模型说一句「looks fine」，一条**没人查过的标准**就被关上了 ——
这是**假闭合**，比不闭合更糟。

- **第 12 项门禁 `content_check`** —— 第 9 项看**引擎**，第 10 项看**报告**，
  第 11 项看**人这条路**，**没有任何门禁看交付物本身的内容**。
  这个缝是用**反证**查出来的：内容路径的九个变异
  （PDF 流不解压 / Office 文档掉进 zip 分支 / XML 标记不剥 /
  内容检查去读字节 / 读不出内容判成「未判定」/ 格式检查恒过 /
  overlay 被忽略 / 门禁自己不再看内容 / 门禁丢了一条观察）
  **九个全部捕获**。

- **新模块 `mas/content.py`** —— 只用标准库，**不执行任何东西**：
  - `detect_format(path)` —— **magic bytes 优先、扩展名兜底**。
    扩展名会撒谎，`.txt` 里装的是 PDF，那它就是 PDF。
  - `extract_text(path)` —— 取出文件里读得出来的文字：
    PDF 解 FlateDecode 流并抽 `(...) Tj/TJ` 字符串；
    `.docx` / `.xlsx` / `.pptx` 解 zip 成员并**剥掉 XML 标记**；
    zip 读成员；图片没有文字但**读得开**。
  - `describe_file(path)` —— 一张事实表：格式、大小，
    以及格式自己的事实（PDF 的**页数**、Office 的**成员数**、
    PNG/GIF/BMP 的**像素宽高**）。
- **六种新的内容级机检**：`content_contains` / `content_not_contains` /
  `content_regex` / `content_lines_min` / `content_min_chars` /
  `content_format_is`。引擎**自动路由**，标准怎么写都行。
- **读不出内容 = 不通过，不是「未判定」** —— 这是这一版最要紧的一条。
  判「未判定」的话，这条标准会**回到模型手里**，模型一句「looks fine」
  就把它关上了。**判不通过**，标准就留在那儿等人来修。
- **要写的新文件按「将要写进去的内容」判** —— overlay 里那份字节就是判据。
- **顺手修掉一个真 bug**：overlay 的键用的是**未解析路径**，机检用的是
  **解析后的路径**。Windows 上短路径（`C:\\Users\\ADMINI~1\\...`）
  和长路径是同一个文件 —— 于是 overlay **永远匹配不上**，机检读的是盘上
  旧文件。两边现在**统一按解析后的路径**配对。
- **门禁自己证明自己会失败** —— 门禁每轮**故意把内容路径打断一次**，
  然后确认自己**注意到了**。不会失败的门禁不算门禁。
- **47 条新测试**（`tests/test_content_770.py` 与 `tests/test_content_gate_770.py`）。

### 循环要能写出二进制交付物

上一版让机检**读得进** PDF，可是**循环写不出** PDF：写文件协议（v0.7.15）
只能带**文字**，worker 的输出是一个字符串，引擎把它写成 UTF-8。
交付物是 PDF 的任务**没有办法产出 PDF** —— 于是**读得进**的那门本事
**没有东西可读**。

- **写文件协议支持 `encoding="base64"`** ——

      <<<MAS:FILE path="out/report.pdf" encoding="base64">>>
      JVBERi0xLjQK...
      <<<MAS:END FILE>>>

  引擎把块里的内容**解码成真正的二进制**再落盘。不写 `encoding` 的块
  **仍旧按文本写**，旧协议一点没变。

- **第 13 项门禁 `binary_write`** —— 第 12 项门禁看**读**，
  **没有任何门禁看「写」**。这个缝是用**反证**查出来的：写路径的
  七个变异（overlay 装的是字节**个数**不是字节 / 编码被忽略 /
  base64 解成文本 / 不认识的编码被接受 / 内容机检不算写义务 /
  坏 base64 被悄悄吞掉 / 计划里装的是编码文本）
  **七个全部捕获**。
- **门禁会打自己** —— 写路径每轮**故意打断一次**，
  然后确认自己**注意到了**；路径**崩了**也算「注意到了」，
  因为一个只会崩的门禁比一个会报的门禁更没用。

### 这一版顺手修掉的真 bug

- **overlay 装的是字节的个数，不是字节** —— 并行选优的 overlay
  取的是 `planned["bytes"]`，那是**长度**。于是每一份文件机检读到的
  都是一个**数字**而不是那个文件：**会写出正确 PDF 的候选，
  输给了会什么都不写的候选**。真实字节在 `planned["data"]` 里。
  现在 overlay 由 `LoopEngine._planned_overlay()` 统一构造，
  **只装 bytes**，装不进 bytes 的一律跳过。
- **base64 静默吞掉垃圾** —— 宽松解码会把**不在字母表里的字符全丢掉**，
  于是一段纯胡话被解成**一个空文件**，一声不吭。worker 要一份 PDF，
  工作区里躺着一个**零字节的 PDF**。现在**严格解码**：
  解不出来就**拒绝**，并说明原因。
- **不认识的编码被默默丢掉** —— `encoding="rot13"` 的块被解析器拒绝，
  但**错误信息没往外传**：没有文件、没有报错、账本里也没有线索。
  现在 `parse_write_blocks_with_errors()` 把理由一起交出去。
- **模板块被当成了真的请求** —— 二进制模板里写着
  `out/report.pdf`，看着像个真路径。靠**路径**认不出模板
  （任务真可能就要写这个文件），所以改用**正文**认：
  模板正文是 `文件内容` / `JVBERi0xLjQK...`。

### 这一版的验证

- **pytest 1091 通过 / 4 跳过 / 95 个 subtest**；
  `unittest discover` **1095 个测试 OK**。
- **13 项门禁**在 `--iterations 120` 下**全过**。
- **七个反证变异全部捕获**。
- **隔离 HOME 的一键安装 E2E**：装完的应用能跑通 13 项门禁，
  并且**端到端产出一个 PDF 交付物**（245 字节，1 页，
  抽出 `(Conclusion 42)`，两条内容级标准都判通过，闭环关闭）。

## [0.7.69] - 2026-11-16

### 在原工作区里改文件，交回后照样验收

v0.7.67 让人写的交付物能闭上环，但那一版只接受**一段文字**。
而这个循环真正跑的任务写的是**文件**，验收标准也是**读磁盘的文件机检**。
人改这样的任务，做的事是**打开文件改**；旧的文本交回却要求人把文件内容
粘进命令行。这是**第二份工作**，而复制的这一份正好是容易出错的那一份。
**忘了一行，交回就被拒绝** —— 而拒绝的原因与答案本身无关。

- **第 11 项门禁 `folder_handoff`** —— 第 9 项门禁看的是**引擎**（人写的东西能不能闭环），
  第 10 项门禁看的是**报告**，**没有任何门禁看人这条路**。这个缝是用**反证**查出来的：
  文件夹交回的六个变异（机检读错文件夹 / 交付物丢文件正文 / 被拒时也写交付物 /
  offer 只取第一个文件 / 交付物落到第二个文件夹 / 票据忘了工作区）
  **一个都没被捕获** —— 前面十项门禁一项都没拿住。
  现在第 11 项门禁看着这条路，**六个变异全部捕获**。

- **文件夹才是交付单位** —— 人在**原地方**改文件，交回的是**那个文件夹**，
  交付物由文件自动生成，**不用手工粘贴**。
- **草稿语义** —— 判定用 overlay（盘上现在的字节），**判定前不写盘**；
  **被拒时磁盘原样不动**，不写交付物 —— 你改的工作不会被丢掉。
- **通过了才写回** —— 验收过的交付物落在**人改过的那个文件夹里**，
  不是第二个文件夹。文件和指名它们的交付物不再分家。
- **票据就是入口** —— `mas tickets` 直接印「在这里改：<工作区>」与
  「改完交回：mas handback <id> --workspace "<path>"」。人不用自己去找。
- **新命令 `mas handback --workspace <dir>`** —— 交回一个文件夹；
  `--file` / `--text` 的旧行为不变。**MCP 工具 `mas_handback`** 也支持文件夹。
- **35 条新测试**（`tests/test_handoff_769.py` 与 `tests/test_folder_handoff_gate_769.py`）守住这些行为，
  包括「旧文本交回承载不了文件内容，新文件夹交回可以」这一条对比。

## [0.7.68] - 2026-11-16

### 报告要看得见「人写的那一步」

v0.7.67 让**人写的交付物也能把闭环关上**。但那份成果在报告里是**看不见的**：
报告只认机器跑出来的记录，人交回的东西在账本里留了一行，却读不出结论。
这一版把这条缝补上 —— 并且**当场抓到两个真 bug**。

- **两个真缺陷（先量出来，再修掉）**：
  - **闭环明明合上了，报告却说 `closed:false`、闭环率 0%**，问题还挂在
    「待处理」里。根因是人工交回的账本条目缺 `closed` / `goal` / `title` /
    `decision` / `files`，而且**分数挂在 handback 行上、报告却只读 closure 行**。
  - **线程闭合语义反了** —— 旧规则是「所有成员运行都闭合才算闭合」，
    于是**失败过一次就永远待处理**，哪怕后来已经成功。现在改成
    **最后一次尝试闭合即闭合**。
- **第 10 项门禁 `handback_report`** —— 第 9 项门禁看的是**引擎**（人写的东西能不能闭合），
  **没有任何门禁看报告层**。这个缝是用**反证**查出来的：报告层的六个变异
  （丢掉 `closed`、决策改名、不读 handback 分数、回到「所有成员都闭合才算」、
  忘掉是哪一次闭的、来源恒说机器）**一个都没被捕获** —— 第 9 项门禁一个都没拿住。
  现在第 10 项门禁看着这一层，**六个变异全部捕获**。

- **报告里的「来源」列** —— 「问题概览」和「运行概览」两张表都多了一列：
  `🤝 人工完成` / `🤖 机器闭环`。一眼看出这次闭环是谁关上的。
- **人工交付物有自己的验收小节** —— 「人工交付物的验收判定：」，
  逐条列出通过/未通过，并附上**人的意见**（human note）。
- **决策词汇分开** —— 机器闭合记 `close`，人工交回记 `handback`，
  两者在账本、报告、replay 里都是**不同的词**。
- **`mas replay` 也看得见人** —— 运行头部打印「🤝 来源：人工交付物」与
  人工意见；每个 step 的角色用双语显示 `人工交回（handback）`。
- **15 条新测试**（`tests/test_handback_report_768.py`）守住这些行为，
  包括「被拒的 handback 仍然要被报告出来」—— 失败不许消失。

## [0.7.67] - 2026-11-16

### 人写的交付物也能把闭环关上

- **第 9 项门禁 `handback_closes`** —— 工单队列以前只有**一个**答案：
  「续跑，让机器再试一次」。但真实世界里最常见的情况恰恰是**人已经把答案
  写好了**，却没有地方放。这一版把这条回路补上，并且让它**被门禁当场抓住**。
- **人写的交付物走的是同一套验收标准** —— 标准是从工单上取回来的，
  **就是这件事当初写下的那套**，不是重新解释一遍，也不是评审员的心情。
  带机检的标准由机检说了算，**评审员不能推翻它**。
- **不合格的接回**不会假装闭合：失败项写回票上，工单**留在人工队列里**。
  **一次被拒的接回是第二次尝试，不是一次结单。**
- **评审挂了也不会毁掉人已经写好的交付物** —— 机检在调用评审之前就已经
  判完了，后端这时挂掉只会把「评审失败」如实记下来，不会让一份人工写好的
  交付物凭空消失。**这条是被探针当场抓出来的**：原来的捕获范围比主循环窄，
  provider 抛一个普通异常就会把手写的东西弄丢。
- **票据记下了那一轮的工作区** —— 人改的是那次运行的文件，验收就必须
  在**同一个文件夹**里看，否则每一项文件检查都会说「文件不存在」。
- **新命令 `mas handback`** —— 队列里每个待处理工单现在都会印出这一行：

  ```
  交回交付物：mas handback T-20261116-075440-8aae --file <你的文件>
  ```

- **MCP 工具 `mas_handback`** —— Codex / Claude 可以直接把交付物交回闭环，
  不用切到终端。
- **门禁离线跑**：不要端点、不要 key、不要网络。

## [0.7.66] - 2026-11-16

### 接手的运行看得见上一轮的成果，并且能换一份预算

- **第 8 项门禁 `takeover_handover`** —— 上一版让票据带齐了上下文，但
  **接手的运行根本用不上那份上下文**。这一版把「接手」这件事本身修好，
  并且让它**被门禁当场抓住**。
- **四个真缺陷（都是先量出来、再修掉的）**：
  1. **token 触顶被误报成「循环自己闭不上」** —— 预算触顶的运行被归到
     `loop_escalation` 队列里，人会去查一个卡住的 worker，而真正的原因是
     预算太小。**现在预算触顶就是 `budget_exhausted`。**
  2. **接手的运行看不见上一轮的交付物** —— 续跑把尝试计数重置成 1、
     问题清单清空，于是 worker 被要求「从零写」，写出来又是同样的东西。
     **现在上一轮的交付物会原样交给接手的 worker**，并明确要求在它基础上改。
  3. **预算触顶的票据，续跑会立刻再次触顶** —— 续跑直接沿用本机配置的
     上限，所以「继续」这三个字从来不是真的继续。**现在三条上限都能按
     这一轮单独给**，且不会漏进同一引擎的下一轮。
  4. **屏幕上那句「接着干」是死路** —— 它印出来的命令继承了同样的上限。
     **现在续跑命令会带上更宽的那一条上限**（外加一个下限，保证宽出来的
     预算真的能干活）。
- **续跑命令的旗标写对了** —— 屏幕上的命令必须是**能粘贴就能跑**的：
  `--max-tokens` 而不是 `--max_tokens`。这条是被门禁当场抓出来的。
- **六项回归反证全部被捕获**：交付物被藏起来、预算覆盖被丢掉、token
  触顶又被归成升级、放宽逻辑被拆掉、接手的运行拿不到东西、票据丢掉预算。
  **一个只终达入不报的门禁不叫门禁。**
- **门禁离线跑**：不要端点、不要 key、不要网络。

## [0.7.65] - 2026-11-16

### 没闭合的运行必须留下人能接手的东西

- **第 7 项门禁 `handover_ticket`** —— 引擎答应过三件事：跑不出来就升级、
  升级就开票据、票据带够让人接回来的上下文。前两件看得见，**第三件从来
  没人守**。现在它会被门禁当场抓住。
- **票据不是留言，是交接单** —— 门禁把票据**真的用起来**：拿票据里的
  验收标准喂回 `engine.resume()`，循环必须真的闭合才算过。
  **verifier 在这个检查里永远说 fail**，所以闭环只能靠票据带回来的
  机检 —— 模型糊弄不过去，票据丢了标准就接不回来。
- **当场抓到一个真 bug** —— 票据的字段在两条升级路径上不一致：预算/错误
  路径带 `status`，停滞路径手写的 payload 不带。人在队列里看到一张
  少了「最后状态」的票据，得先分辨自己看的是哪种失败才知道发生了什么。
  **根因修好了**：两条路径现在共用一个 payload 构造器，**票据只有一个形状**。
- **六项回归反证全部被捕获**：票据丢了验收标准、标准只剩散文、票据丢掉
  最后产物、票据丢掉失败原因、票据被提前标记为已处理、票据根本不落盘。
  **一个只终达入不报的门禁不叫门禁。**
- 门禁同时检查账本与票据**互相指得到对方**：账本里的 run_id 找得到，
  账本里也有指向这张票据的记录。

## [0.7.64] - 2026-11-16

### 并行选优必须是确定的

- **第 6 项门禁 `parallel_pick`** —— 引擎的文档写着“赢家是确定的”，
  但这条不变量一直没有被守住：候选池按完成顺序回填结果、平局反向打破、
  或者排名把正文长度排在机检之前，都会换一个交付物交出去，而运行仍然
  报成功。**现在它会被门禁当场抓住。**
- **赛次是被排好的，不是靠运气** —— 检查用事件链把三个候选的完成顺序
  钉死成逆序（`concise` → `thorough` → `direct`），所以结果跟调度器
  的心情无关。
- **三个候选让选优必须真干活** —— `direct` 与 `thorough` 通过的机检数
  与正文长度**完全相同**，赢家只能靠最小序号分出；`concise` 更长但
  一条机检都过不了。**谁赢是排出来的，不是抢出来的。**
- **四项回归反证全部被捕获**：候选池按完成顺序回填、平局反向打破、
  长度压过机检、赢家取“先跑完的那个”。**一个只终达入不报的门禁不叫门禁。**
- 门禁同时检查落盘的 `deliverable.md` —— 账本说选了这个，工作区里就得
  是这个。

## [0.7.63] - 2026-11-16

### 装完即自证（门禁接进安装流程）

- **安装脚本现在会自己跑门禁** —— `install.ps1` 与 `install.sh` 在装完后
  自动执行一次 `mas selftest --iterations 20`。**装坏了当场看得见**，
  而不是等到用户第一次真跑任务才发现。
- **两项新门禁**（都是离线，全程不要端点、不要 key、不要网络）：
  1. `mcp_handshake` —— MCP 握手能应答，广告出来的工具都答得上来，
     调不存在的工具会被拒绝，跑完一轮服务还活着；
  2. `stream_folds` —— 两种事件流方言（chat 分片 / Responses 事件）
     折叠成同一段正文，且 `total_tokens` 不丢。
- **四项回归反证全部被捕获**：MCP 不再广告 `mas_run`、MCP 不再回应
  `ping`、SSE 折叠把用量扔掉、SSE 折叠丢一段正文。**一个只终达入不报
  的门禁不叫门禁。**
- 新测试 `tests/test_selftest_gate_763.py`，独立文件，不碰既有测试的
  import 顺序。

## [0.7.62] - 2026-11-16

### 隔失门禁：重复打磈易偶发的路径

- **两个真实的少见结果进了 CI**：中继拒绝路径时没有排空请求体（v0.7.59），
  以及已计费的失败调用丢了用量。两者都是“测一次看不出来”的少见结果，
  一次性的测试本来就抹涂不了它们。
- **新增 `mas selftest`**：把“把一条路径打上千次看不变量”这个方法变成一条命令。
  全程离线：自己在本机起回环表服务器，不要端点、不要 key、不要网络。
- **三项保证**：
  1. 中继拒绝越界路径不会毒染同一连接（同一 socket 上的下一个请求仍有正确答案）；
  2. 已计费的失败调用照样带着用量；
  3. 一次运行的产物都在同一个文件夹。
- **门禁本身被反证过**：三个回归分别把中继、用量计账、工作区放坏打破，
  三次全部被捕获——一个只终达入不报的门禁不叫门禁。
- CI 新增一个步骤：`python -m mas selftest --iterations 120`。

## [0.7.61] - 2026-11-16

### 产物不再散落（统一工作区）

- **每次运行一个自己的文件夹**：`<state_dir>/artifacts/<run_id>/`。
  以前运行的产物写在调用方当前目录（自动化运行里通常是临时目录，进程
  一结束就没了），交付物写在另一个地方；现在两者在同一处，跑完就留得住。
- **用户指定的 `--workspace` 仍然优先**：用户点名了目录，就用那个目录，
  不偷换。
- **交付物改名** `deliverable.md`，放在运行目录里，和它写的文件放在一起。
- **账本记录** `workspace` 与 `files`（本次生成的文件清单）。
- **`mas run` / `mas resume` / `mas artifacts`** 都会显示本次生成的文件。

### 兼容性

- `mas artifacts` 仍按交付物列出全部运行，只是路径深了一层。
- 未指定 workspace 的运行，产物从「调用方目录」改为「运行自己的目录」。

## [0.7.60] - 2026-11-15

### Added

- 新增角色词典（`role_reading`）：planner/write/review/closure/reflector/run_end 等引擎内部角色名都有对应人话
- `mas ledger` 列表改为「验收 (closure) 第1轮 通过 (pass) 闭环了 (close)」——人话在前，原名在后
- `mas export` 导出的表格新增「判定」「决策」两列
- `mas artifacts` 列表的状态列改为人话

### Fixed

- 加固 provider 用量测试：传输层失败与响应形状错误是两回事，只有后者才可能携带 usage，测试不再因连接重置误报

## [0.7.59] - 2026-11-15

### Fixed

- 修复 relay 拒绝请求时未排空请求体的问题：HTTP/1.1 keep-alive 下残留字节会污染下一次请求，客户端偶发收到连接中断而不是 404（v0.7.58 引入的 CI 失败根因，本地难以复现、压测 200 次约 9 次触发）
- relay 的 401/404 拒绝路径改为先排空 body 再应答，并显式关闭该连接

### Added

- 新增两条 relay 回归测试：连续拒绝不会弄断连接、拒绝之后下一次正常请求照常返回

## [0.7.58] - 2026-11-15

### Added

Why the loop did not close now says so, in words.

v0.7.57 made the *verdict* speak plainly. But the line that matters
most to a beginner is the one that says why the run stopped - and
that line was still the engine talking to itself:

    [FAIL] closure  not closed (status budget_exhausted): budget: reached max_iterations=1
    - unclosed reason: budget: reached max_iterations=1

"budget: reached max_iterations=1" says *that* something happened, not
*what* happened: not that the loop used up its rounds, not how many
rounds it had, and not what to do about it. A reader who does not know
what `max_iterations` is learns nothing at all from the line.

Every reason the engine can write now reads in plain language, and
names the command that would raise the ceiling:

    status: budget ran out (not finished - stopped for you to take over) (budget_exhausted)
    reason: budget ran out: this round was allowed 1 round, and it used them all.
      to raise the round count: mas init --max-iterations <a bigger number>

The reason is read on every screen where a human looks for it: `mas
run`, `mas resume`, `mas tickets`, `mas report` and `mas replay`. The
ledger keeps its English - the reason is part of the audit record, and
scripts read it.

### Added

* `mas init` gained the three ceilings the reasons point at:
  `--deadline-sec`, `--max-tokens` and `--stagnation-limit`. Before
  this release the reading named a command that did not exist, so the
  reader was told to run something that failed. The reason said
  "raise the ceiling" and the ceiling had no switch.
* `mas replay` shows each step in words, not only in the exported
  table.

### Fixed

* `mas/reason_text.py` translated reason strings the engine **never
  writes** - `budget: exceeded max_tokens=` - while the strings the
  engine *does* write (`budget: tokens {used} >= {limit}` and
  `budget: wall-clock budget exhausted after {n}s`) had no reading at
  all. The two most common ways a run stops were the two the
  translator missed.
* The comparison direction was reversed. `length 48 < 999999` was
  translated into "the body is only 48 characters, which *exceeds*
  999999" - the exact opposite of the truth. A reader went looking
  for the wrong problem. The same bug affected the byte count.
* The reason line could not be read on one line: the sentence and the
  "raise the ceiling" hint were joined by a line break, so a
  one-line display (the ticket queue) broke apart. The hint now lives
  in its own function and sits on its own line.
* A blocked issue that named a standard with no description of its own
  was printed with a placeholder in front of it, which buried the one
  thing the issue said: what actually went wrong. It now falls back to
  the plain note, with the serial number still attached.
* The reason of a resumed run kept the English of the run it
  resumed, in the middle of a Chinese sentence.

## [0.7.57] - 2026-11-15

### Added

The verdict now says why, in words.

v0.7.56 gave every verdict the standard back in words. But the verdict
itself was still the engine talking to itself:

    [PASS] c1 (rule) all required strings present
          要求：正文里要出现「结论」

Two lines, saying the same thing twice, in a language you did not
write in - and the first line was the one your eye landed on.

Every verdict now reads in plain language, and names where it came
from:

    [PASS] c1 机检：所有要求的字符串都在
          要求：正文里要出现「结论」
    [FAIL] c1 文件检查：文件的修改时间 2026-11-15T11:17:06+00:00 不晚于 2099-01-01T00:00:00+00:00
          要求：文件 out/report.md 的修改时间要晚于 2099-01-01T00:00:00Z

The note is translated wherever the engine writes one - the length of
the text, the missing string, the regex that did not match, the sha
that differs, the section that is not there, the file that is missing.
A note the translator does not recognise is **left alone**, never
replaced with a guess: an unknown note may carry information nobody
can see.

### Changed

* The ledger keeps its English. The notes are part of the audit record
  and scripts read them, so the translation happens at the display
  layer only - a stored run still means exactly what it meant when it
  was stored.
* `mas resume` now says the verdict the same way `mas run` does. A
  resumed run is read by the same person in the same session, and the
  two screens must not disagree about what happened.
* `mas report` says the status and the decision in words - "已闭环
  （closed）", "预算用完了（budget_exhausted）" - and keeps the raw
  value in parentheses, because that is what a reader greps for.
* The ticket queue names the standard that is blocked instead of the
  serial number the engine made up: `c1: unjudged: ...` becomes
  "正文里要出现「结论」- 没有人判定".

### Fixed

* The status vocabulary did not cover the states the engine actually
  writes when a run does not close (`escalated`, `not_closed`,
  `incomplete`, `deliverable_ok_but_not_closed`), so the one line a
  reader scans to see what went wrong was still in English.
* The same for the sources a verdict comes from (`model-missing`,
  `skipped`, `model`).
* The report's "nobody judged it" line showed the raw source value
  with no words at all.

## [0.7.56] - 2026-11-15

### Added

The acceptance criteria now say what they asked for.

When you write three standards and run the loop, the verdict used to
come back as

    [PASS] c1 (rule) all required strings present
    [FAIL] c2 (rule) required string missing

`c1` is not one of your standards - it is a serial number the engine
made up. `all required strings present` is the engine talking to
itself. Nothing in that output told you *which* of the standards you
wrote was checked, and nothing told you what "required" meant for the
one it named.

Every verdict now carries the standard back in words:

    [PASS] c1 (rule) all required strings present
          要求：正文里要出现「结论」
    [FAIL] c2 (file) mtime 2026-11-15T11:14:06+00:00 is not newer than 2099-01-01T00:00:00+00:00
          要求：文件 out/report.md 的修改时间要晚于 2099-01-01T00:00:00Z

Three kinds of standard read three different ways, because they mean
three different things:

* a **written description** wins - if you wrote "must have a conclusion
  section", that sentence is kept, and the machine rule is appended so
  you can see what will actually be checked;
* a **bare shorthand** (``contains:结论``) is replaced by its
  reading instead of being echoed twice - the raw text told you nothing
  you did not already know;
* an **optional** standard is marked as optional, so you can tell
  "this did not block" from "this was never checked".

The reading is also stored in the ledger next to the score, so
`mas report` and `mas report --json` quote the same standard back.

### Fixed

A file-level check printed its argument as a Python dict.

The file checks carry their argument as ``{"path": ..., "value": ...}``,
and the first version of the reading stringified whatever it was given.
So the requirement line came out as

    文件 {'path': 'out/report.md', 'value': '2099-01-01T00:00:00Z'} 的修改时间要晚于 {'path': ...}

which is a repr, not a sentence. The file checks now have their own
templates and their argument is resolved before formatting, so the path
and the expectation each get their own place in the sentence.

### Tests

* `tests/test_criteria_talk_to_people.py` - the reading of every check
  type, the precedence between a written description and the machine
  rule, the optional marker, the CLI line and the report line.
* `tests/test_file_check_reading.py` - the dict leak, pinned: no
  file check may print a brace or a quote.

## [0.7.55] - 2026-11-15

### Changed

The three commands a beginner meets first now answer back.

`mas status`, `mas demo` and `mas report` are what a new user runs before
they have done anything at all - and all three used to talk to the
machine instead of the person:

* **`mas status` said "provider: mock".** "mock" is not a word a
  newcomer knows, and the page it printed was a column of zeroes. Zeroes
  are not a diagnosis: they do not say whether the tool is broken or
  simply has not been used yet. The status page now names the offline
  demonstration mode in words ("闭环会跑，但调的是内置假模型，不联网、
  不花钱"), says how to switch to a real model, and - on a machine that
  has never run anything - says explicitly that the zeroes are not a
  failure.

* **`mas demo` printed "expect=closed actual=closed" three times.** That
  is the engine talking to itself. The three scenarios are the proof
  that the loop actually works, so each one now says what it proves:
  the loop re-does a deliverable until it passes the criteria; the loop
  stops and opens a ticket instead of burning the budget when it is
  stuck; the loop reports an exhausted budget instead of pretending it
  finished. The demo also says that it is offline - no network, no real
  model, no cost - and ends with the next command.

* **`mas report` ended with "账本为空，暂无运行记录。"** True, and a dead
  end. The report now says it is not a failure and names the two
  commands that fill the ledger.

## [0.7.54] - 2026-11-15

### Changed

`mas doctor` now speaks to a person instead of to a machine.

The self-check printed one line per check - a name, a status, a path -
and then exited zero. On a clean machine that is eight OK lines and six
WARN lines, and the exit code says *fine*. A beginner who had just
installed the tool had no way to tell that the six warnings meant "no
skill installed, no MCP server registered, the agent cannot reach this
tool at all" - the very thing they installed it for.

Three changes:

* **Every check says what it is.** Each row now carries a plain-language
  line ("是什么：给 Codex 装的 skill：让 Codex 会用这个闭环工具"), so a
  machine name like `skill_codex` or `mcp_claude` is never the only
  thing on the line.

* **Checks are sorted into three tiers.** `必需` (the loop cannot run
  without it), `建议` (the loop runs, but a real user should act on it),
  and `可选` (the loop runs fine without it; it only makes the loop
  reachable from an agent). The config file used to be labelled
  required *and* reported as a warning at the same time - a
  contradiction, now fixed by giving it its own tier.

* **The report ends with one conclusion and one next command.**
  `verdict()` answers the only question a beginner actually has:
  `broken` (the loop cannot run), `degraded` (it runs, but a part did
  not install), `agent_not_reachable` (it runs, but no agent can reach
  it), or `ready`. The `action` field names the command to run next.
  `loop_usable` and `agent_reachable` are reported separately, because
  "the tool is broken" and "the tool is fine but nothing can call it"
  are different problems with different fixes.

The exit code now means what it says: non-zero only when the loop
itself is broken. The optional parts stay warnings, so `mas doctor`
remains exit-zero on a clean CI runner.

`mas doctor --json` carries the verdict next to the counts and checks,
so a script can ask "can the loop run?" instead of parsing prose.

## [0.7.53] - 2026-11-15

### Fixed

Four commands could tell you a run did not exist when the truth was
that the ledger held twenty-two records for a *different* run. The
message was the same in both cases - "the ledger is empty" - and it is
the message a beginner reads first. A mistyped run id, or a ticket
that points at a run from another machine, both landed on the same
sentence, and that sentence pointed at the wrong thing.

Measured on the live machine: `mas replay --run-id R-does-not-exist`
answered "账本为空，没有可回放的运行。" while the ledger held twenty-two
rows. `mas export --run-id R-does-not-exist` printed a table header
and nothing else. `mas report --run-id R-does-not-exist` wrote "账本为空，
暂无运行记录。" into a full Markdown report.

Two more gaps sat next to them:

* **`mas export` accepted `--json` and ignored it.** A script that
  asked for JSON got a Markdown table and had to parse prose. The flag
  was advertised and did nothing.

* **An empty ledger produced an empty table.** `mas export` on a fresh
  installation printed a header row and stopped - a table that says
  nothing, to a reader who has just installed the tool.

* **A fix for the Linux build.** v0.7.52's new test asserted that
  `Ledger` and `ledger` are the same directory. That is true on
  Windows and false on Linux and macOS, where they are two different
  paths - so the Linux build failed while every local run passed. The
  guard was right; the test was wrong. The test now states the
  platform's own spelling rule instead of assuming one platform.

* **The commands say what exists.** A run id that does not exist now
  answers with the runs the ledger *does* hold, so the reader can fix
  the typo instead of wondering whether the work ever happened:

  ```
  没有找到运行 R-does-not-exist。
    账本里有这些运行：R-7e4af659ba
    用 mas status 看一眼有哪些运行，用 mas replay --run-id <运行ID> 回放其中一次。
  ```

* **"Not found" and "empty" are different things.** An empty ledger
  still says the ledger is empty; a ledger that holds other runs says
  which ones. The two can no longer be confused.

* **The miss exits non-zero and speaks JSON.** `--json` returns
  `{"error": "run_not_found", "found": false, ...}` and the exit code
  is 2, so a script can tell a miss from a hit without parsing prose.

* **`mas export --json` returns data.** It now carries the rows, the
  count and the Markdown rendering, and the text and JSON forms are
  guaranteed to agree.

* **An empty ledger says what to do next.** `mas export` on a fresh
  installation now says the ledger is empty and points at `mas run`
  and `mas demo`, instead of printing a bare header row.

* **An empty ledger in `--json` is still JSON.** `mas replay --json`
  on a fresh installation returns a well-formed object with
  `error: empty_ledger`, not empty stdout with a non-zero exit code.

Regression tests: 13 in a dedicated module, plus a mutation framework
that reverts each of the eight changes on its own and confirms the
matching test fails.

## [0.7.52] - 2026-11-15

### Fixed

The ledger is the only thing that makes a loop-engineering run worth
anything, and it can vanish. The user's own configuration file pointed at
`C:\Users\ADMINI~1\AppData\Local\Temp\mas-handover-n2d4xr0z` - a
directory that `tempfile.mkdtemp()` had created for one test run. The
operating system cleans the temp folder whenever it feels like it, and
when that happened the ledger went with it: the tool reported an empty
history for work that had actually happened, and every new run started
from zero. Nothing crashed, nothing warned. The work was simply gone.

Two guards were supposed to prevent this, and neither did.

* **The guard recognised one spelling of "temporary".** It matched the
  literal `tmpXXXXXXXX` shape that `mkdtemp(prefix="tmp")` produces. The
  project's own test suite never used that prefix - it used
  `mas-handover-`, `mas-state-`, `mas-ledger-`, `mas-envstate-`,
  `mas-wizard-home-` - so every single one of them walked straight past
  the guard and wrote its throw-away directory into the user's real
  configuration.

* **The guard compared strings.** It fired only when `MAS_STATE_DIR`
  happened to be character-for-character equal to the value in the file.
  The same directory spelled `C:\Users\ADMINI~1\...` in one place and
  `C:\Users\Administrator\...` in another is still the same directory,
  and the second spelling slipped through.

Measured on the live machine: after a full test run the real
`~/.mas/config.json` said `"state_dir":
"C:\Users\ADMINI~1\AppData\Local\Temp\mas-handover-n2d4xr0z"`. A
second full run pointed it somewhere else. The user's ledger was
somewhere under the temp folder, one cleanup away from gone.

* **The guard now looks at the random tail, not the word in front of
  it.** A directory under the temp folder whose name ends in eight
  random characters after a separator is treated as disposable,
  whatever the prefix was. The prefix has to end in a separator so that
  a folder a person named themselves - `%TEMP%\myproject` - is still
  their own and is kept.

* **The guard now compares paths, not strings.** `pathlib.Path.resolve()`
  expands the 8.3 short form, eats a trailing slash and normalises the
  case, so the same directory in two spellings is recognised as one.

* **A directory the user named is a decision, not an injection.**
  `mas init --state-dir <dir>` keeps the directory even when it lives
  under the temp folder - a person who typed a path meant it. The guard
  is there to stop an injection, not to overrule a decision.

* **An environment-injected state directory is never written to disk.**
  `MAS_STATE_DIR` decides where the *current* run writes; it does not
  become a permanent fact about the installation.

* **The test suite can no longer poison the next test.** Several test
  modules pointed `MAS_STATE_DIR` at a directory of their own and forgot
  to put it back, so a later test read a throw-away directory as if it
  were the user's configuration. The guard now wraps
  `unittest.TestCase.run()` - the one entry point every test goes
  through - so it holds under the standard-library runner as well as
  under pytest, no matter how a subclass overrides `setUp`.

Regression tests: 22 in a dedicated module, plus a mutation framework
that reverts each of the seven changes on its own and confirms the
matching test fails.

## [0.7.51] - 2026-11-15

### Fixed

The replay was the one place that still spoke only in runs. `mas report`
learned to talk about problems in v0.7.50, but `mas replay` printed a
flat list of ledger rows - and a replay is exactly what a person reads
when they want to know what the loop did and what they have to do next.
Twenty rows for two attempts on one problem read exactly like twenty
rows for two separate problems. And the replay stopped at the last line
of the trace: an escalated run replayed fine, showed what went wrong,
and never said where to pick it up. The report got a handover hint in
v0.7.49; the replay left the gap open.

Measured on the live model: after one resume the replay printed a single
flat block of fourteen rows with no run boundaries, no grouping and no
handover line, even though the ticket was waiting in the queue.

* **The replay groups by run.** Each run is a block with its own status,
  round count and token spend, and the task it was about. A reader can
  see where one attempt ended and the next began.

* **The replay hands the open problem to the reader.** An unfinished run
  prints the exact command to continue it:
  `→ 接着干：mas resume T-... --note "你的意见"`.

* **The replay groups by problem.** A closing section says which
  attempts belong to which problem, whether it is still open, and how
  many rounds it has cost - one line per problem, not one per attempt.

* **The report leads with the problems.** The Markdown report now opens
  with a "问题概览" table (problem, state, attempts, rounds, ticket,
  tokens) *before* the run table, because the run table lists every
  attempt and a reader who stops there concludes there is more to do
  than there really is.

* **The machine-readable replay says the same thing.** `mas replay
  --json` now carries `runs`, `threads` and `problems_open` next to the
  steps, and every step knows its `run_id`.

## [0.7.50] - 2026-11-15

### Fixed

The ledger counts *runs*; the human queue counts *problems*. A run
that escalates opens a ticket, and every resume on that ticket makes
another run on the same problem. The status page and the report then
counted the runs - so one unresolved problem that was resumed three
times showed up as "未闭环 4" next to a queue of one ticket. The report
and the queue disagreed about how much work was left, and the ticket
itself said "已尝试 5 轮" when fifteen rounds had actually been spent.

Measured on the live model: after one resume the status page said
"未闭环 2" while the ticket queue held exactly one ticket, and the
ticket reported only the last attempt's round count. The loop reported
a problem four times over, and the human could not tell one problem
from four.

* **One problem is one thread.** The report and the status page now
  count *problems* next to *runs*, and every run that belongs to a
  ticket is grouped under that ticket. Two runs on one problem read
  as two runs and one problem.

* **The ticket counts the rounds of every attempt.** It carries the
  cumulative number of rounds the human has paid for, not just the
  last attempt's, so a problem that has been resumed three times says
  how much it has really cost.

* **The ticket names the runs it covers.** A ticket that was resumed
  twice lists both runs, so the queue is a join between the human
  side and the ledger.

* **The ledger books the thread.** Every resumed run records the
  ticket it belongs to, and a resumed run that closes keeps that
  ticket - the closing run is part of the story of the problem it
  solved, not a run that belongs to nobody.

* **A resolved ticket points at the run that closed it.** The
  deliverable the human actually got is on the ticket, next to the
  failed history that led to it.

## [0.7.49] - 2026-11-15

### Fixed

Only the ``escalate`` ending opened a human ticket. Every other
unfinished ending left *nothing* behind: a run that stopped on the
iteration ceiling, that ran out of wall-clock time, that ran out of
tokens, or that died on a provider error ended with a status and no
ticket at all.

Measured on the live model: a run that hit ``max_iterations`` ended as
``budget_exhausted`` with the deliverable on disk and **no ticket**.
The status page said "未闭环 1" and the human queue was empty - the
loop reported an unfinished job and offered nobody a way to finish
it. That is the one link of the loop that was missing: escalate ->
handle it by hand -> resume had no entry point for the run that ran
out of road.

* **Every ending that is not "closed" now opens a ticket.** The run
  that stops on the budget ceiling, and the run that dies on a
  provider error, both leave something a human can pick up.

* **The ticket says which kind of failure it was.** A run that hit the
  ceiling gets a ``budget_exhausted`` ticket; a run that died on the
  backend gets a ``run_error`` ticket. "Raise the budget" and "fix
  the endpoint" are different human reactions, and a ticket that
  calls both of them the same thing cannot tell the human which one
  happened.

* **The ticket carries the reason, the goal, the criteria and the
  last deliverable** - everything needed to decide whether to resume,
  without re-running anything.

* **A closed run still leaves no ticket**, and a resumed run still
  keeps the ticket it already has: one problem is never listed twice.

### Tests

* ``tests/test_every_unfinished_run_leaves_a_ticket.py`` - 14 new
  regression tests.
* ``work/_rc749.py`` - a seven-layer falsification framework
  (AA~GG); every layer is caught by the tests.

## [0.7.48] - 2026-11-15

### Added

``mas tickets`` printed one line per ticket - an id, a state, a kind
and a title - and ``mas ledger`` printed *nothing at all* on an empty
state. A command that prints nothing looks like a command that broke,
and a ticket line that names only a serial number cannot be acted on.

* **A ticket now explains itself.** Every ticket in the queue shows
  the task it belongs to, the run that produced it, the check that
  failed, the reason the loop gave up, how many rounds it spent, the
  size of the last deliverable, the human note, and the exact
  ``mas resume`` command that continues it.

* **An empty command says it is empty.** ``mas ledger`` on a fresh
  install says the ledger is empty, names the ledger file and shows
  the command that fills it. ``mas tickets`` says the queue is empty
  and points at ``mas demo``. A command that says nothing is
  indistinguishable from a command that failed.

* **The ledger tells you where it is.** After the entries, it prints
  the total and the path of the full ledger.

* **Resolved tickets are shown separately** from the waiting ones,
  with the decision and the note the human left, and without a resume
  hint - a resolved ticket must not tell the user to resume it.

* **The status page names the problem, not just the number.** The
  waiting-ticket line carries the ticket's task next to its id, so a
  person can tell three waiting escalations apart.

### Tests

* ``tests/test_tickets_readable.py`` - 23 new regression tests.
* ``work/_rc748.py`` - a nine-layer falsification framework
  (AA~II); every layer is caught by the tests.

## [0.7.47] - 2026-11-15

### Added

``mas status`` printed a raw JSON payload and nothing else. That is
fine for a machine and useless for the beginner this project is written
for: the JSON says ``entries: 27`` and ``waiting_human: [T-...]`` and
never says "you have 2 deliverables and 1 ticket waiting for you". A
status page a person cannot read is not a status page.

* **The status page talks to a human.** The default output is now a
  short plain-text page: the provider and model, the state directory,
  the ledger size, how many runs happened and how many closed, the
  deliverables with their paths, the tickets waiting for a human, and
  the budget. ``--json`` still gives the machine-readable payload.

* **The status page counts the work.** It now says "运行 N 次 / 已闭环
  N / 未闭环 N" - ``entries: 27`` says how many lines the log has,
  "3 runs, 2 closed" says how the work went.

* **The status page lists the deliverables.** With their titles, their
  sizes and their paths, and it says so when a file is missing.

* **The status page points at the waiting tickets.** It prints each
  ticket with the exact ``mas resume`` command to continue it. An
  escalation that the user cannot find is an escalation that never
  gets handled.

* **The JSON payload grew the same sections.** ``runs`` carries the
  total, the closed count and the open count; ``artifacts`` carries
  the count, how many files are missing and the items themselves.

### Tests

Eleven new tests in ``tests/test_status_readable.py`` cover the
change: the default output is not a JSON dump, it names the provider,
counts the runs, lists the deliverables, names the waiting tickets
with the resume command, says when there is none, says the budget,
calls out a missing file, and the JSON output carries both new
sections while keeping the old ones. Seven layers were counter-proven
by reverting each on its own.

## [0.7.46] - 2026-11-15

### Added

v0.7.45 made the deliverable findable. v0.7.46 makes it *legible*.
The gap was measured against the live model, not guessed: **the ledger
recorded everything about a run except the one thing a human needs -
which task produced these results**. A replay could say "a run closed,
241 characters, sha fe8848013b" and the reader had no way to learn
whether it was the onboarding guide or the Q3 report.

The mock backend had hidden this, because its planner summary happens
to echo the goal - so the gap only appears against a real model.

* **The run's end entry books the task.** It now carries ``goal`` and
  ``title``. A ledger that cannot name its own work is a log of calls,
  not a record of work.

* **The summary reads it back.** ``run_summary`` exposes ``goal`` and
  ``title``, so every consumer of the ledger - the report, the CLI,
  the JSON output - can say what a run was about.

* **The report names the task.** The overview table gained a "任务"
  column, and each run's section now opens with "- 任务：<goal>".
  A table of run ids and hashes is an audit log; a table that also says
  what was being done is a report a human can read.

* **``mas artifacts`` names the task.** The listing shows the goal
  above the path, and the JSON rows carry ``title`` and ``goal``. A
  list of paths is not a list the user can act on: they would have to
  open every file to find out what it is.

* **A pipe in the goal does not break the table.** The overview cell
  escapes ``|``, so a goal like "写 A | B" cannot split the row.

### Tests

Eleven new tests in ``tests/test_run_task_recorded.py`` cover the
chain: the end entry books the goal and the title, the title falls back
to the goal, the summary carries both, the report names the task in the
overview and in the run section, the overview table has a task column,
a pipe in the goal is escaped, and the artifacts listing and its JSON
output name the task and tell two runs apart. Seven layers were
counter-proven by reverting each on its own and confirming the matching
tests fail.

## [0.7.45] - 2026-11-15

### Added

v0.7.44 made the deliverable survive the process. v0.7.45 makes it
*reachable*. The gap was measured, not guessed: the CLI printed a
1500-character **preview** of the artifact and the ledger path - never
the path of the file a human can actually open - and nothing at all
could list the deliverables of previous runs. A beginner who ran one
loop, closed the terminal and came back a week later had no way to ask
the only question that matters then: "what did I actually get?"

* **``mas run`` and ``mas resume`` name the file.** Both now print
  ``交付物：<path>`` under the ledger line. ``resume`` matters most:
  a resumed loop is the expected path for an escalated run, so its
  result was the one most likely to be lost.

* **``mas artifacts`` lists every deliverable this machine produced.**
  New subcommand. It reads the ledger, groups the runs and prints one
  line per deliverable: the run id, the status, the character count,
  the number of rounds, the path, and whether the file is still there.
  ``--run-id`` narrows it to a single run.

* **``mas artifacts --json`` gives the same rows as data.**
  ``{"artifacts": [...], "count": N}``, each row carrying ``run_id``,
  ``status``, ``path``, ``exists``, ``chars``, ``tokens``,
  ``iterations`` and ``finished_at``.

* **A missing file is called out, not hidden.** If a deliverable was
  deleted or moved, the listing says so - "缺失" next to the row and a
  warning at the bottom. A listing that silently dropped the rows it
  could not open would be a listing that lies about what exists.

* **An empty state says what to do.** Instead of printing nothing, the
  command says "还没有任何交付物。跑一次 `mas run` 就会生成。"

### Tests

Fifteen new tests in ``tests/test_artifacts_cli.py`` cover the path:
the run announces the file, the announced file exists and is the one
the report names, ``resume`` announces it too, the JSON output carries
it, the listing counts, filters, reports missing files, and says what
to do when there is nothing yet. Six layers were counter-proven by
reverting each on its own and confirming the matching tests fail.

## [0.7.44] - 2026-11-15

### Added

A closed loop is supposed to hand something over. The deliverable
lived in the engine's memory and nowhere else: the ledger booked its
hash, the report printed its hash, and the moment the process ended the
work was gone. A beginner who ran ``mas run``, saw a green light and
closed the terminal had a verdict and no deliverable.

* **The deliverable is written to disk.** Every run now saves its
  final artifact under ``<state_dir>/artifacts/<run_id>.md``, next to
  the ledger, named after the run. The engine writes it before the run
  is booked as ended, so a run that says "closed" has always left a
  file behind.

* **The path is booked on the run's end entry.** The ledger carries
  ``artifact_path`` and ``artifact_chars``, so a replay can find the
  work without guessing. ``RunResult`` carries the path too, and the
  JSON output of ``mas run`` includes it.

* **The report names the file.** The report used to print a hash and
  stop there - a hash is a fingerprint, not something a human can open.
  It now says "交付物已保存：`<path>`（N 字，可直接打开取用）", so the
  report points at the work and not only at its fingerprint.

* **A failed write does not take the run down.** The work is done and
  the artifact is in memory; a closed loop that lost its file to a
  permission problem would be reported as a crash of the loop itself.
  The write is best effort, and the run is unaffected.

### Tests

Nine new tests in ``tests/test_deliverable_persists.py`` cover the
chain: the deliverable is written to disk, the file holds the whole
artifact, it lives next to the ledger, the ledger books the path and
its length, the report names the file, the result dict carries it, two
runs get two different files, and a run that produced nothing invents
no path. Six layers were counter-proven by reverting each on its own
and confirming the matching tests fail.

## [0.7.43] - 2026-11-15

### Added

The engine writes one closure row per iteration and one reflector row
per failed iteration - the loop's own account of itself. The report
read only the *last* closure row, so a run that revised itself three
times showed a single verdict: the last one. A report of a loop that
shows only its last turn is a report of a single shot.

* **The report now shows every round.** The summary gained a
  ``timeline``: one entry per judged round, carrying the verdict, the
  decision, how many criteria passed, how many were never judged, the
  issues of that round, the stagnation counter and the hash of the
  deliverable that round produced. The Markdown renders it as
  "循环过程（共 N 轮）" with one line per round, so a reader watches
  the loop work instead of only reading its last verdict. A run that
  closed on the first attempt says so plainly ("1 轮即定") rather than
  pretending there was a process.

* **The reflection is visible.** The reflector is the role that says
  *why* the next attempt must change, and the report never mentioned
  it. Each round now carries the reflection that led into it, so cause
  and effect sit next to each other, and the summary gained a
  ``reflections`` list with the summary, the issues and the next action
  of each one.

* **The replay's reflector row is no longer blank.** ``_short_detail``
  had no branch for the reflector, so the one line of the trace that
  carries the loop's own reasoning was empty. It now renders the
  reflection summary, its issues and the next action.

### Tests

Twelve new tests in ``tests/test_loop_timeline_report.py`` cover the
chain: the summary carries every round, the failed round survives in
the timeline, the passing round survives too, the reflection is
attached to the round it produced, the reflections are listed, the
Markdown shows every round, the first round's failure is visible, the
reflection is visible, a single-round run says so, the replay's
reflector row is not blank, the replay trace carries the reflection,
and a run without reflections has no reflection section. Seven layers
were counter-proven by reverting each on its own and confirming the
matching tests fail.

## [0.7.42] - 2026-11-15

### Fixed

The engine keeps three states per acceptance criterion - it passed, it
failed, or nobody could judge it - and it is careful about the third
one: an unjudged criterion never passes, and it never closes the loop
on its own. The report threw the distinction away at the last step and
turned three states into two.

* **A criterion nobody had judged was reported as a failure.** The
  report's failed list was built with ``not s.get("pass")``, which is
  true for both ``False`` and ``None``. Every criterion that no machine
  check, no reviewer and no lead could decide landed under "failed" and
  was rendered as ``[FAIL]``. A reader was told a criterion that was
  never checked had been checked and rejected - the exact opposite of
  what the engine knew. The failed lists now use ``pass is False``,
  which is the only thing that means "judged and rejected".

* **The deliverable line made the same false claim.** A run whose
  deliverable was never fully checked was rendered as "交付物：❌ 未通过
  验收标准" - a clean rejection that never happened. When some
  criterion was never judged, the report now says so and names the
  criteria: the deliverable was not rejected, it was never fully
  checked.

* **The Markdown had no third mark.** The acceptance table rendered
  ``PASS`` or ``FAIL`` and nothing else. It now renders ``UNDECIDED``
  for a criterion nobody could judge, with the note that it cannot
  count as a pass, and the report lists the unjudged criteria separately
  with the source of the verdict ("rule", "file", "model", "lead",
  "model-missing") so the reader can see *why* nothing could decide.

* **The summary now carries the unjudged criteria.** ``run_summary``
  gained an ``unjudged`` key next to ``failed`` and
  ``optional_failed``, so a consumer of the JSON report can tell
  "checked and rejected" from "never checked" without parsing prose.

### Tests

Eight new tests in ``tests/test_unjudged_report.py`` cover the chain:
the unjudged criterion is kept out of the failed list, it is carried in
its own list with its verdict source, a genuinely failed criterion still
fails, an optional failure still stays optional, the Markdown renders
``UNDECIDED`` and never ``[FAIL]`` for it, the unjudged section names
the criteria, the deliverable line refuses to claim a clean rejection,
and a pure failure still reports one. Every layer was counter-proven by
reverting it on its own and confirming the matching test fails.

## [0.7.41] - 2026-11-15

### Fixed

The loop's last link was written in the engine and never reached the
report. When a run escalates, the engine opens a ticket that holds the
deliverable, the criteria and the history, and ``mas resume`` picks the
work up from there. But the ticket number was never written to the
ledger, so the report could not name it.

* **The ticket number never entered the ledger.** The engine's end
  entry carried the status, the error and the deliverable hash, but not
  the ticket the escalation had just opened. The report therefore had
  nothing to say beyond "escalated", and a human reading the report had
  no way to find the ticket that held the work. The ticket number is
  now booked on the run's end entry.

* **The report could not point at the ticket.** The summary now carries
  the ticket number, and the rendered report names it together with
  what to do with it: ``mas tickets`` to look at it, ``mas resume
  <ticket>`` to continue from there. An escalated run now tells the
  human where to act, not merely that something went wrong.

### Tests

Four new tests cover the chain: the summary carries the ticket, a run
without one reports an empty ticket rather than a wrong one, the
Markdown names the ticket and the next step, and the engine really
writes the ticket into the ledger (an end-to-end check over a run that
actually escalates). Every layer was counter-proven by reverting it on
its own and confirming the matching test fails.

## [0.7.40] - 2026-11-15

### Fixed

The engine explains why a run ended, and the report dropped the
explanation. Since v0.7.37 the engine names the cause of a stagnation
escalation precisely - "the deliverable did not change for N
consecutive attempts" - and the report rendered neither the reason nor
the stagnation count. An escalated run therefore looked the same
whatever went wrong.

* **The report never said why the run did not close.** The engine
  books the reason on the run's end entry; the report read the entry
  for the status but dropped the reason on the floor. The rendered
  report now carries it as "未闭合原因", and an escalation whose reason
  the ledger does not record says so instead of staying mute.

* **The stagnation count was thrown away.** The engine counts how many
  consecutive attempts produced the *same* deliverable - that count is
  what tells "the worker is stuck" apart from "the worker needs
  another round". The report now reports it, states it in words, and
  shows it in the overview table next to every run, so a reader
  scanning the table can see which run was stuck.

### Tests

Six new tests cover the projection: the summary carries the stagnation
count and the end reason, a run that never stagnated reports zero, the
Markdown shows both, the overview table carries the count, and an
escalation without a recorded reason says so rather than guessing.
Every layer was counter-proven by reverting it on its own and
confirming the matching test fails - the overview table initially
had no coverage at all, and the counter-proof for it caught that gap
before the release.

## [0.7.39] - 2026-11-15

### Fixed

A run that did not close has two very different causes, and the report
could not tell them apart. The engine answers both questions separately
- *did the deliverable pass every check* and *did the run close* - and
books the first answer on the run's end entry. The report threw that
answer away.

* **A good deliverable that ran out of budget looked like a bad
  deliverable.** A real run against a live endpoint produced a
  deliverable that passed every acceptance criterion, then stopped on
  the stagnation ceiling and escalated. The report said "not closed" -
  as if the work itself were wrong - and the user had to guess which
  half of the system had failed. The report now reads the engine's own
  answer and reports such a run as ``deliverable_ok_but_not_closed``:
  a distinguishable state, not a third word for "unfinished".

* **The Markdown said nothing about which half failed.** Even where the
  numbers were available, the rendered report listed the status and the
  token spend and stopped there. It now says it in words: the
  deliverable passed every criterion but the run did not close (budget
  or deadline), and the artifact is usable as is.

* **"The run never said" is not "the deliverable was bad".** A run
  with no end entry did not know how its deliverable fared, and the
  report now reports ``deliverable_ok: None`` for it rather than a
  confident ``False``.

### Tests

Five new tests cover the projection: a good deliverable that ran out of
budget gets its own state, a bad deliverable does not, a closed run is
never relabelled, a run that never ended says it did not know, and the
Markdown says which half failed. Every layer was counter-proven by
reverting it on its own and confirming the matching test fails.

## [0.7.38] - 2026-11-15

### Fixed

The lead role had a contract but no wiring. ``roles.py`` described a lead
agent, ``providers.py`` had a mock branch for it, the README and the
skill both listed it as the role that closes the loop - and the engine
never called it. A run whose criteria nobody could judge simply spun
until it stagnated and escalated, which told the human nothing except
that nobody could decide.

* **The lead was never called.** The engine decided the outcome with
  ``decision = "close" if passed else "revise"`` and no lead was ever
  consulted, so the role that the documentation names as the one who
  closes the loop existed only as prose. The lead is now consulted
  exactly when there is something for it to decide: some acceptance
  criteria carry no machine check and no reviewer verdict either. It
  receives the unjudged criteria and the deliverable, and answers with
  ``{"decision", "reason", "judged": [{"id", "met", "note"}]}``.

* **The lead is an advisor, not a judge of facts.** A machine check
  that failed is a fact: the lead is not consulted at all in that case,
  and when it is consulted it may only fill a gap - a criterion the
  machine checks already decided is left exactly as the check decided
  it, whatever the lead says. The engine keeps the final word over
  stagnation, budget and deadline, so a lead cannot close a run that
  has run out of budget.

* **A lead that shrugs closes nothing.** When the lead's ``met`` field
  cannot be read as a boolean, the criterion stays unjudged and the
  loop keeps asking - the same rule that governs an unreadable
  reviewer answer, applied to the advisor. A broken lead (an
  unreachable endpoint or a garbled reply) leaves the criteria
  unjudged and the run continues as it did before; a missing advisor
  does not weaken the engine's own verdict.

* **The lead's call was not in the book.** The lead is a paid call,
  but its role was missing from the ledger's charge list, so every run
  the lead was consulted in under-reported its spend. The role is now
  charged, and the ledger's total equals the run's reported token
  usage.

### Tests

Five new engine tests cover the wiring: the lead is called when nobody
could judge, the lead cannot overrule a failed machine check, the lead
only fills the gap a machine check left, a lead that cannot decide
closes nothing, and the lead's call is booked in the ledger. Every
layer of the change was counter-proven by reverting it on its own and
confirming the matching test fails.

## [0.7.37] - 2026-11-15

### Fixed

A reviewer that answered with a fraction, a garbled word or a shape the
engine did not recognise could close a loop that should have stayed
open - and the report said the run had passed.

* **A score is not a verdict.** The conversion from a model's answer to
  a boolean used ``bool(value)``, so ``0.5`` - a reviewer saying "half
  of this criterion is satisfied" - became ``True``: the criterion was
  booked as a full pass and the loop closed on it. A number is now only
  a verdict when it is exactly ``0`` or ``1``. Everything else is
  *unreadable*, and an unreadable answer is no answer.

* **An unreadable answer was silently booked as a failure.** The
  normaliser folds an unreadable verdict into ``pass: False``, and the
  engine could not tell that apart from a reviewer who actively said
  "no". So the criterion looked *judged*, the engine never asked the
  reviewer again, and the loop burned an iteration on a revision that
  could not help. The unreadable marker is now read on the way in: the
  criterion stays unjudged, the follow-up question is asked, and the
  answer that comes back is the one the engine decides on.

* **The panel path treated a garbled reply differently from the single
  reviewer path.** The multi-reviewer aggregate dropped the unreadable
  marker, so the same bad answer was re-asked with one reviewer
  configured and booked as a decided failure with three. The aggregate
  now carries the marker, and both paths behave the same way.

* **The alternative verdict shape was never held to the same rule.**
  ``normalize_verdict`` rewrites a model's own answer into the
  canonical shape, and it has two paths: ``{"scores": [...]}`` and
  ``{"criteria": [...]}``. v0.7.12 made the summary obey the
  per-criterion answers on the canonical path and left the alternative
  path alone - so a reviewer that wrote ``FAIL`` on every criterion
  while putting ``"verdict": "pass"`` on top still produced a green
  summary. Both paths now agree.

* **A summary cannot light up an unreadable review.** When nothing in
  the scores could be read, the model's own summary was kept - so
  ``{"verdict": "pass", "scores": [{"pass": "maybe"}]}`` reported a
  pass over a criterion nobody judged. The engine decides the closure
  from the per-criterion answers, and the report now says the same
  thing: nothing was judged, so nothing passed.

* **The iteration count in a report was sometimes ``null``.** The
  expression was ``a or b if rows else 0``, which parses as
  ``(a or b) if rows else 0`` - so a run whose rows carry no iteration
  field reported ``null`` instead of a count, and a report that says
  "null iterations" cannot be read by a script. The count is now
  resolved from the run's own end entry, then the closure row, then the
  highest iteration number any row mentions - and it is always a
  number.

* **An escalation that did not say why.** A run that stopped because
  the deliverable stopped changing escalated with the reason "the loop
  could not close itself" - which sends the human looking for the wrong
  thing. It now names the cause: the artifact did not change for N
  consecutive attempts.

### Verified

* ``pytest -q``: 548 passed, 4 skipped, 51 subtests passed
* ``python -m unittest discover -s tests``: Ran 552 tests, OK (skipped=4)
* ``ruff check .``: All checks passed
* Ledger invariant sweep (45 shapes): MISMATCHES: 0
* Counter-proof for every layer above: each change was reverted in
  isolation and the matching test was confirmed to fail

## [0.7.36] - 2026-11-14

### Fixed

The self-check a beginner runs right after the one-click install could
take minutes without a single word, and the deadline it was given was
not a deadline at all.

* **``deadline_sec`` was not a ceiling.** The wall-clock budget was
  only checked *between* iterations. A single provider call that
  overran the budget - a slow endpoint, a long answer - was allowed to
  finish, and the run still reported a successful closed loop. Measured
  on a real endpoint: a run with a 1 second deadline against a 3 second
  provider returned after 9.0 seconds and reported success; a run with
  a 180 second deadline returned after 185.26 seconds and reported
  success. The number in the configuration was documentation, not
  enforcement.

  Every provider call now carries the remaining wall-clock budget as a
  hard ceiling. The HTTP layer takes the smaller of the configured
  timeout and the remaining budget, recomputes it on every retry, and
  raises a distinct ``BudgetExceeded`` when the budget is gone - which
  is a deliberate stop, not a broken endpoint, and is reported as such.
  The closure decision itself is checked against the clock: a deliverable
  that passes every machine check but arrives after the deadline is
  escalated, not closed, with the reason spelled out. The engine also
  no longer swallows an ``escalate`` verdict just because the checks
  passed - previously the "everything passed" branch returned
  unconditionally, so an escalation could be decided and then discarded.

* **The self-check was silent for minutes.** A measured real run took
  460.5 seconds with no output at all, which looks exactly like a hung
  process. ``mas verify`` now announces every slow step before it
  happens - the endpoint handshake, the instruction probe, the closed
  loop - and reports the elapsed time when the loop ends. The progress
  goes to stderr, so the ``--json`` contract of the command stays
  machine readable.

* **The self-check judged a configuration the user did not run.** The
  verify run overrode the user's own wall-clock budget with a hardcoded
  180 seconds. The measured real run took 342 seconds against the
  user's 900 second budget and was reported as "not closed" - the check
  failed a configuration that would have run fine. The self-check now
  uses the budget the user actually configured (capped at 900 seconds so
  it cannot sit silent for a quarter of an hour either).

* **"Not closed" hid which half of the system failed.** A run whose
  deliverable passed every machine check but which overran its budget
  was reported exactly like a run whose deliverable was garbage. The
  user went looking for a broken answer that did not exist while the
  real problem - the clock - stayed unnamed. The engine now tracks the
  two facts separately: `deliverable_ok` says whether the artifact
  passed the checks, `status` says whether the run closed. The self
  check says which one it was: "the deliverable passed every machine
  check, but the run exceeded its time budget" names the clock and
  tells the user what to change.

### Added

* ``mas/ceiling.py`` - a thread-local call ceiling. The budget is
  passed to the provider through the call context rather than through a
  new keyword argument on ``Provider.complete``, so every existing
  custom provider and plugin keeps working unchanged. The ceiling is
  carried explicitly into parallel workers, because a thread pool does
  not inherit thread-local state.

### Tests

* ``tests/test_engine.py::TestWallClockDeadlineIsACeiling`` - a run that
  overruns its deadline must not close; the remaining budget must reach
  every provider call; a run inside its deadline must still close.
* ``tests/test_setup_bench.py::TestVerifyProgressFeedback`` - the slow
  steps must announce themselves before they run, the completion notice
  must come last, and a broken progress sink must not break the check.

## [0.7.35] - 2026-11-14

### Fixed

``mas verify`` could report a configuration the user never made.

* **The endpoint handshake ran without the key.** v0.7.34 made the
  provider read its credential from the environment variable named by
  ``api_key_env`` - the same contract the setup wizard writes to disk.
  The handshake did not follow: ``probe_from_config`` still read only the
  *literal* ``api_key`` field, which the wizard deliberately leaves empty
  so that no secret is ever written to disk. Against an endpoint that
  requires authentication the handshake therefore went out with an empty
  ``Authorization`` header and came back ``401`` - while every real
  request from the very same configuration carried the key and worked.
  The beginner was told to fix a setup that was already correct, and
  ``mas doctor`` and ``mas verify`` disagreed about the same machine.

  The handshake now resolves its credential exactly like the runtime does.
  Both the provider check and the instruction check of ``mas verify``
  and the endpoint check of ``mas doctor`` speak the same dialect, with
  the same credentials, so a diagnosis is a diagnosis of the user's own
  configuration.

  Reproduced against a local server that rejects unauthenticated
  requests: before the fix the endpoint check reported ``HTTP 401`` while
  the server logged the key on every other request of the same run;
  after the fix the endpoint check reports the endpoint as reachable and
  the closed loop closes.

## [0.7.34] - 2026-11-14

### Fixed

The self-check that a beginner runs right after the one-click install
could not tell them what was actually wrong, and the key they were told
to set was never sent to the model.

* **The key from the environment variable was never used.** The setup
  wizard tells the user to put the key into ``OPENAI_API_KEY`` (or
  ``ANTHROPIC_API_KEY``, or whatever name they choose) and records that
  *name* in the configuration - so that no secret is ever written to
  disk. But the provider builder only ever read the literal ``api_key``
  field of the configuration, and that field is deliberately empty. The
  result: a user who had configured everything exactly as documented
  talked to the endpoint with an empty ``Authorization`` header and got
  401s. The key is now resolved through the same environment lookup the
  rest of the configuration already promises, and the resolution is
  tested.

* **A fallback backend inherited the primary backend's secret.** A
  fallback that declared its own key variable still received the
  primary's literal key. Sending the primary's credential to a
  different endpoint is both wrong and a leak; a fallback that names its
  own credential now drops the inherited one, and a fallback that names
  none keeps the shared one.

* **The provider line of ``mas verify`` was a rubber stamp.** It
  printed the configured provider and model and reported "ok" whenever
  the *strings* looked plausible - no network call, no key lookup. A
  user with a dead endpoint got a green provider line and a red closure
  line, with nothing in between naming the real cause. The provider line
  now states the endpoint and whether the key was found, and two real
  checks were added next to it: an endpoint handshake (the same one
  ``mas doctor`` uses, so both commands agree on what "reachable" means)
  and an instruction check that proves the system instruction actually
  reaches the model. A dead endpoint now says so by name.

### Changed

* The provider check in ``mas verify`` also reports whether the key was
  found, so "my key is not being read" is a diagnosis the user can read
  off the self-check instead of having to guess.

* The mock backend stays completely offline: no handshake, no
  instruction probe, no network call at all.

### Tests

* The broken-backend test of ``mas verify`` used to define a broken
  provider and never use it - it ran the mock backend and only checked
  that a check named "closure" existed. The one thing a beginner relies
  on after a one-click install had no regression protection at all. It
  is now a real test: a backend that raises ``ProviderError("endpoint
  down")`` has to be reported as a failure, with the reason attached.

## [0.7.33] - 2026-11-14

### Fixed

The replay of a run was unreadable exactly where it mattered most, and
the single-reviewer mode - the default - described a review that nobody
could interpret.

* **The closure row of a closed run replayed as a blank line.** The
  closure row is the one that says what was judged and how it came out,
  but its summary was built from the issues alone, and a closed run has
  no issues. The successful case - the case anybody replays to see what
  a good run looks like - showed an empty cell. The summary now names
  the verdict, the decision, how many criteria passed, and which ones
  failed and why.

* **The single-reviewer mode reported "usable: None, skipped: None,
  missing: None".** The one-reviewer path returned the raw verdict and
  never went through the panel aggregation, so the ledger and the report
  could not say whether the one reviewer had answered, whether it had
  stayed silent, or whether the review had not run at all. A panel of
  one is described like a panel of one: the numbers are now there.

* **A silent reviewer was not counted as a missing vote.** In the
  single-reviewer mode a reviewer that produced nothing to judge left
  no trace. It is now counted as skipped and missing, exactly as it is
  in a panel of several.

### Changed

* The replay and the raw ledger table share one summary function, so
  both describe a run the same way. No row of a real run is blank any
  more.

* The panel description does not change the closure decision. A
  criterion with a machine check is still decided by the machine check,
  whether the reviewer spoke or not.

## [0.7.32] - 2026-11-14

### Fixed

The report could not tell "stopped" from "failed" from "unfinished".
The engine never wrote its final status into the ledger, so the report
had to *guess* the status from the closure rows - and the guess was
wrong for every run that did not end on a decision.

* **A run that stopped on the budget ceiling was reported as "not
  closed".** That reads as "still unfinished", which is a different
  thing entirely: the loop stopped, on purpose, with a reason. The
  report now says ``budget_exhausted``.

* **A run that died on a provider error was reported as
  "incomplete".** The error was nowhere in the report. A run that
  failed cannot be mistaken for a run that was never finished.

* **The artifact of a failed run was invisible.** The artifact hash
  lived on the closure row, and a run that failed before it was ever
  scored has no closure row - so the report showed an empty artifact
  and the reader could not find out what was actually made.

* **The reason for the end was not shown.** The report now carries the
  engine's own account of the ending: the status, the decision, the
  error, the issue list, the iteration count and the spend.

### Added

* **The ledger states how the run ended.** Every run now ends with a
  ``run_end`` entry that carries the status, the decision, the error,
  the number of iterations, the artifact hash, the unresolved issues
  and the total spend. The report reads that entry instead of
  guessing, and the raw ledger table names the ending of every run.

* ``run_summary()`` gained ``error`` and ``ended_at``.

### Changed

* The end entry is the engine's own account of the run, so it wins
  over the closure rows wherever both exist. Ledgers written by older
  versions have no such entry and still read correctly - the report
  falls back to the old behaviour for them.

* ``run_end`` is not a charge. The spend of a run is summed over the
  call entries; the end entry carries the running total as a
  checkpoint, exactly like the ``closure`` entry does.

## [0.7.31] - 2026-11-14

### Fixed

The ledger could not answer "how much did this run spend". The figure
only existed in the engine's memory, so a replay report built from the
ledger had no cost in it at all.

* **The single worker path wrote no ledger entry at all.** The parallel
  candidate pool booked its own spend, the single worker did not - so
  the money the worker spent was nowhere in the book. It is booked now,
  under the same role as the parallel pool.

* **The reflector was never booked.** The reflector runs after every
  failed iteration and it is a paid call, but the ledger never mentioned
  it: the book showed a run that revised itself again and again and
  never said what the reflection cost. It is booked now.

* **Only the last review round was booked.** The first review round and
  the follow-up question are two separate paid calls; only the final
  aggregate carried a number. The two rounds are booked separately now,
  and a token is never counted twice.

* **The report had no cost in it.** ``run_summary()`` and
  ``build_report()`` gained a ``tokens`` field, and the Markdown report
  shows the spend of every run and of the report as a whole.

### Changed

* ``mas.report.ledger_spend()`` is the single place that sums the
  charges of a ledger. ``closure`` entries carry the running total
  (``tokens_total``) as a checkpoint, not a charge - summing them on
  top of the call entries would double the bill.

* Fourteen regression tests. Verified: reverting each fix one at a time
  makes the corresponding test fail. The invariant sweep now covers
  45 pool shapes (1-4 workers x 1-3 reviewers, across clean runs,
  truncations, failures and mixes) and checks that the endpoint's bill,
  the engine's total and the ledger's sum agree to the token.

## [0.7.30] - 2026-11-14

### Fixed

The follow-up question - the cheap re-ask for the criteria a reviewer
did not judge. It behaved one way in the panel mode and another way in
the single reviewer mode, and it lied to the ledger in one of them.

* **A run that closed on the follow-up answer was audited as a run whose
  review had judged nothing.** In the single reviewer mode the ledger
  kept the *old* verdict after the re-ask, so the book showed a review
  with no scores at all next to a closed run. The panel mode already
  carried the re-ask forward; this mode now does the same. The ledger
  describes the panel the engine actually decided on.

* **A failed follow-up question ended the whole run.** The re-ask is a
  cheap supplementary question - if it fails, the criterion simply stays
  unjudged and the loop handles it like any other unresolved criterion.
  The panel mode already degraded that way. The single reviewer mode let
  the error escape, so the same missing verdict crashed one setup and
  merely slowed the other down. The failure is now charged to the
  budget, recorded in the ledger, and the loop continues.

* **The failed follow-up call was free.** The call was billed before it
  failed to answer, so its cost is charged to the budget now.

### Changed

* Six regression tests. Verified: reverting each fix one at a time
  makes the corresponding test fail (three and two respectively).

## [0.7.29] - 2026-11-14

### Fixed

The accounting of a *failed* call, and the pool that threw it away.

* **A failed candidate was charged only if it happened to fail before
  the winner.** The spend of the pool members that failed was flushed
  when the winner was reached, so a candidate that failed *after* the
  winner in the list was never charged. The same pool cost a different
  amount depending on which member happened to finish first - and a
  budget that depends on the race is not a budget. The spend is now
  collected in the same pre-pass as the truncation and charged once,
  after the ranking, so the total no longer depends on the order.

* **A discarded pool did not charge the member that failed.** When a
  truncation invalidated the whole pool, the truncated member and the
  members that had finished were charged - but the one that failed
  outright was not. Every member of a pool that is thrown away was
  billed, and all of them are charged now.

* **A discarded review round did not charge the reviewer that failed.**
  The same rule for the review panel: when a truncation threw the round
  away, the reviewer that failed outright was left out of the charge.
  It is charged now, in the first review round and in the re-ask round.

* **A failed call that was billed did not say so.** `ProviderError` now
  carries the usage of the call that failed, so a broken response
  shape, a stream that died halfway, or a failover chain that fell over
  to the next backend reaches the engine with its cost attached. A run
  that died on a provider error used to report a spend of zero for work
  it had already paid for.

### Changed

* The invariant sweep now covers 50 pool shapes - from a single worker
  to four workers with three reviewers, across clean runs, truncated
  answers, failed calls and mixtures of both. Every one of them books
  exactly what the endpoint charged.

## [0.7.28] - 2026-11-14

### Fixed

Three holes in the token accounting, and one loop that could never close.

* **A thrown-away candidate pool was charged as if it had cost nothing.**
  When one candidate in a parallel pool was cut off by the output limit,
  the whole pool was discarded and retried - but only the *truncated*
  candidate was charged. The candidates that had already finished were
  free. Measured on the real endpoint: the ledger reported 6,000 tokens
  for a run that had actually spent 16,010. The token ceiling was
  systematically too generous, and the budget was not a budget. The
  pool now charges every candidate, finished or cut off.

* **The planner and the reflector were never charged at all.** Both are
  paid calls whose cost never reached the budget - the planner on the
  first iteration, the reflector on every failed one. A run that
  planned, wrote, failed, reflected and reviewed reported 922 tokens for
  1,033 tokens of real spend. Both are charged now, and the ledger shows
  what the planner call cost.

* **A reviewer that ran out of output room was counted as an absent
  voter.** A truncated reviewer is not a reviewer who disagreed - it is a
  reviewer that never finished. The strict panel counted it as a
  missing vote, so the loop could never close: every iteration burned
  the whole budget and the run ended in an escalation for a reason that
  was one more retry away. A truncation in the review panel is now a
  retryable failure with a bigger output budget on the next attempt -
  the same treatment the worker path already got. The re-ask round gets
  the same rule.

### Added

* Ten regression tests: four on the token accounting (planner, reflector,
  discarded pool, ledger) and six on the truncated reviewer (retry,
  bigger budget, ledger entry, absent-vote semantics, budget charge,
  and a panel that never answers still ending the run with a clear
  reason).

## [0.7.27] - 2026-11-14

### Fixed

Three defects that made a loop close wrong - or fail to close at all.

* **A list-shaped file check could never close the loop.** The engine
  parsed the check argument with its own logic while the machine check
  used a different parser. The two disagreed on the list form: the check
  saw `notes/a.txt`, the engine saw the literal text `['notes/a.txt']`.
  The worker was told to write a file with brackets in its name while
  the check looked at another path - so the loop never closed, no matter
  how many iterations it ran. The engine now reads the path with the
  check's own parser, so the two can never disagree.

* **A recovered run reported the error it had already survived.** A run
  that was truncated, retried and then closed carried `status` `closed`
  *and* `error` `truncated answer: ...`. The CLI printed the error as an
  explanation, which reads like a failed run. A transient failure now
  belongs to the iteration it happened in - closing clears it - while the
  ledger keeps its `truncated` entry, so the audit trail loses nothing.

* **The token ceiling was not a ceiling.** The budget was only checked at
  the end of an iteration, and the truncation retry runs before that. A
  run that kept hitting the output limit could spend far past the
  ceiling the user set (measured: ceiling 200,000, spent 240,000). The
  ceiling is now checked at the top of every iteration as well, so it
  really is a ceiling.

### Added

* Seven regression tests: three on the write-path argument (list, tuple
  and object form), two on the recovered run (no error on a closed run,
  the truncation still in the ledger) and two on the token ceiling.
  Verified: with each fix reverted, 2 + 2 + 1 tests fail.

### Verified

* `pytest tests/` - 434 passed, 4 skipped.
* `unittest discover -s tests` - Ran 438 tests, OK.
* `ruff check mas tests` - all checks passed.
* Live run against the local bridge (`claude-opus-5-5`): a two-candidate
  parallel run with a file-level check closed on the first iteration, the
  file really landed on disk and the check read the real file. The same
  task with the list-shaped argument - the case that used to deadlock -
  also closed on the first iteration.

## [0.7.26] - 2026-11-14

### Fixed

The engine caught `ProviderError` and nothing else. Any other exception
raised inside the loop escaped `run()` entirely - and a run that ends
without a ledger entry is a run that cannot be audited.

* **An unexpected error erased the audit trail.** The ledger showed a
  run that started and never ended: no error entry, no closure record,
  no ticket. The closure state of that run was simply unknowable, which
  is the one thing this project exists to make knowable.

* **The engine was not a safe library entry point.** The CLI, the MCP
  tool and the HTTP route each have their own exception boundary, so the
  user saw a clean error message. But the engine is also called
  directly - by a library user, by a script, by anything embedding the
  runtime - and there it raised a bare traceback.

* The loop now catches everything that is not a `ProviderError`,
  records it in the ledger as an `internal` error and returns a normal
  `error` result. The distinction is kept: a provider failure still
  reads `provider error`, and the criterion validation still raises its
  `ValueError` before the loop starts, because a broken rule must never
  look like a closed run.

### Added

* Seven regression tests: the run does not raise, the error is reported
  on the result, the ledger records it with the `internal` kind, the run
  stays auditable, the parallel path and the broken-worker path are
  contained, and a provider failure still reads as a provider failure.
  Verified: with the containment removed six of the seven fail.

## [0.7.25] - 2026-11-14

### Fixed

The truncation handling added in v0.7.24 threw the truncated answer
away - including the tokens the endpoint had already spent to produce
it. The budget therefore under-reported the real cost of a run that had
to retry.

* **A truncated call was free.** The endpoint generated tokens, billed
  them, and then stopped. The exception carried the answer but not the
  usage, so the engine charged the call nothing. A run that truncated
  five times looked five times cheaper than it was, and the budget
  ceiling was effectively five times too generous.

* **The parallel pool under-reported by a factor of N.** With three
  workers, three calls were truncated and three sets of tokens were
  spent - but only the single re-raised truncation was counted. The
  other two candidates' tokens vanished. The whole pool's spend is now
  collected before the truncation is raised, so the number in the ledger
  is the number the endpoint billed.

* `TruncatedAnswer` now carries the usage of the call that produced it,
  and the engine charges it to the budget. The ledger's `truncated`
  entry reports the tokens that were spent.

### Added

* Two regression tests: the truncated call is charged to the budget, and
  every truncated candidate of a parallel pool is charged. Verified:
  with the charging removed the parallel test reports 0 tokens instead
  of the expected 2400.

## [0.7.24] - 2026-11-14

### Fixed

A cut off answer was delivered as if it were a finished one. The
endpoint's `finish_reason` was captured, put into the raw payload and
then never read again - so the one signal that says *this answer is
incomplete* was silently dropped.

* **A truncated answer could close the loop.** A model that stops at the
  output token limit produces a *partial* answer - and a partial answer
  can satisfy the acceptance checks by accident: the searched string may
  already be in the first paragraph, and the fragment may already be long
  enough for `min_chars`. The loop then closed on the fragment, reported
  `closed` and handed a half-written deliverable to the user. This is
  the same family of false closure this project exists to prevent, and it
  was the most likely one to happen in practice.

* **The output budget was never raised.** The provider default of 4000
  output tokens was used for every call, and it was never increased. A
  deliverable that does not fit in 4000 tokens was therefore always cut
  off - and always silently accepted.

* **A truncated answer is now a retryable iteration failure.** The
  providers raise `TruncatedAnswer` instead of returning an incomplete
  answer, the engine records the truncation in the ledger and retries the
  iteration. The partial answer is discarded on purpose: a fragment that
  happens to satisfy a check must not become the deliverable.

* **The retry gets more room.** The output budget doubles with every
  attempt (8000, 16000, 32000 - capped), so the loop gets a real chance
  to finish the answer instead of failing the same way twice. The budget
  is configurable with `output_max_tokens`.

* **The parallel path behaves the same.** A truncated candidate is not
  counted as a lost angle: the whole pool is retried. Otherwise the loop
  would rank a pool of fragments and pick the longest one.

### Added

* `mas.providers.TruncatedAnswer`, plus `is_truncated` and
  `finish_reason_of` for reading the signal out of a chat-completions or
  an Anthropic Messages answer.
* Thirteen regression tests: eight on the provider side (the signal, the
  two providers, the complete-answer case) and five on the engine side
  (no false closure, the fragment is never delivered, the ledger entry,
  the growing budget, the parallel path). Verified: with the detection
  removed one provider test fails, and with the engine retry removed all
  five engine tests fail.

## [0.7.23] - 2026-11-14

### Fixed

The reasoning stripper only understood the *closed* shape of a
reasoning block, and only the `<think>` spelling of it. Two real cases
slipped through and the scratchpad was delivered again.

* **An unclosed reasoning block was delivered whole.** When a model
  runs out of tokens in the middle of its scratchpad, the closing tag
  never arrives - the answer is a `<think>` that never ends. The stripper
  matched only `<think>...</think>`, so the whole truncated scratchpad
  was delivered as the artifact, was counted in the length tie break and
  was written into the workspace files. An unclosed block now drops
  everything from the opening tag on: there is no deliverable after an
  opening tag that was never closed.

* **Other gateways spell the tag differently.** `<thinking>` was
  already handled; `<reasoning>`, `<scratchpad>` and `<reflection>` were
  not, so on those endpoints the scratchpad reached the deliverable
  unchanged. All five spellings are recognised now, closed and unclosed.

* The closed case is still handled first, so a *closed* block is never
  mistaken for an opening one and the real answer behind it survives.

### Added

* Nine regression tests covering the unclosed block, every tag
  spelling, the closed-block-after-open interaction and the plain text
  case. Verified: with the unclosed rule removed two tests fail, and
  with the extra spellings removed three more fail.

## [0.7.22] - 2026-11-14

### Fixed

The parallel candidate path forgot to strip the reasoning block. A
reasoning model wraps its scratchpad in `<think>...</think>`. The single
worker path has always stripped it before the artifact was judged,
written or delivered - the parallel path did not.

* **The scratchpad was delivered as the artifact.** What the user
  received, what the verifier was asked to judge and what was written
  into the workspace files was the model's private reasoning, prefixed
  to the real answer. The single worker path was never affected.

* **The scratchpad decided the tie break.** All candidates failing the
  checks equally, the winner was chosen by the length of the artifact.
  A candidate with a longer scratchpad therefore won the race - the
  ranking was effectively measuring how much the model thought, not how
  good its answer was.

* **The scratchpad polluted the text level checks.** `min_chars` counted
  the reasoning as content, and `contains` was satisfied by a token that
  only appeared in the reasoning.

* The parallel path now strips the reasoning block *before* the ranking,
  which is what makes the two paths agree: the same input yields the
  same artifact whether one worker or three produce it.

### Added

* Regression tests asserting that the parallel artifact carries no
  reasoning block, that the single and the parallel runs deliver
  identical content, and that the reasoning length cannot decide the tie
  break. Verified: with the strip removed the candidate grows from 34 to
  369 characters and the tests fail immediately.

## [0.7.21] - 2026-11-14

### Fixed

The parallel candidate ranking compared the candidates against the *old*
contents of the workspace. The file level checks read the real disk, and
at ranking time no candidate had written its file yet - so every file
level check failed for every candidate, and the file rules had no
influence on the choice at all.

* **The winner was picked by length, not by the rules.** With every
  candidate scoring zero on the file rules, the tie was broken by the
  length of the artifact. A candidate that would write a file violating
  the rule could win the race, and the loop would then fail - or worse,
  close on a file that a different candidate had produced. The single
  worker path was never affected: it writes first and checks second, in
  the right order. Only the parallel path checked before it wrote.

* **The ranking now scores a candidate against the file it would
  produce.** A candidate's write blocks are parsed into a *plan* without
  touching the disk, and the file checks read that plan through an
  overlay. The winner is then written to disk as before - and only the
  winner. The disk state during the ranking is never modified, so the
  candidates cannot see each other's output.

* `mas.filewrite.plan_writes` and the `overlay` argument of
  `mas.filechecks.evaluate_file_check` are the two new building blocks.
  An overlay is a plain mapping from absolute path to bytes; it changes
  what a check *reads* and nothing else.

### Added

* Regression tests for the write order: three candidates write different
  contents to the same file, the wrong ones deliberately *longer*, and
  the test asserts that the winner is the one whose file satisfies the
  rule and that the disk holds that winner's content. With the overlay
  removed the test fails - the long wrong candidate wins and the right
  one is discarded - so the test provably catches the defect.
* Tests for the overlay itself: it replaces the file contents, it makes
  a missing file exist, and a path outside the overlay is still read
  from the disk.

## [0.7.20] - 2026-11-14

### Fixed

The parallel candidate ranking scored the candidates against the wrong
directory. The winner of the race is picked on that ranking, so the wrong
directory did not just mislabel a score - it decided *which* candidate
became the deliverable.

* **The ranking ran without the configuration.** The candidate scoring
  called the check evaluator without the configuration, and the file level
  checks resolve their paths against the workspace *in the configuration*.
  With no configuration the workspace falls back to the current working
  directory. Every file level check therefore ran against a directory that
  the run was not using, every candidate scored zero on the file rules, and
  the ranking degenerated into a tie broken by length - so a candidate that
  violated the file rule could win the race, and the file rules could not
  influence the choice at all. The single worker path and the final
  verdict always passed the configuration; only the ranking forgot it.
  The ranking now passes the configuration too, so all three paths resolve
  the same workspace.

* Note that the *final* verdict was never affected: it is recomputed with
  the configuration after the winner is chosen. The damage was limited to
  the choice of candidate - but that is exactly what the race exists for.

### Added

* A regression test that runs the race with the workspace in a different
  directory than the current working directory, with three candidates that
  answer differently, and asserts that the winner is the one that passes
  the file rule. Reverting the fix makes the test fail with every
  candidate at zero passes - the test is proven to catch the defect, not
  merely to run.

## [0.7.19] - 2026-11-14

### Fixed

The role contract of the loop was silently discarded on Responses-style
gateways. This is the most damaging defect this project has shipped, and it
was found by running the loop against a real Codex Responses bridge instead
of a mock.

* **The system instruction never reached the model.** The provider sent
  `messages` (the chat-completions shape) *and* `input` (the Responses
  shape). A Responses-style bridge reads `input` and ignores `messages`
  entirely - and `input` used to carry only the user prompt. The system
  instruction was therefore dropped on the floor on every such gateway.
  The visible symptom was subtle: the endpoint answered normally, `mas
  doctor` reported the endpoint as healthy, and the loop still "closed" -
  but the verdicts were guesswork. The worker's "output the deliverable
  itself", the verifier's JSON schema, the reflector's and the lead's
  decision contract: none of them were in effect. A closed loop built on
  a discarded contract is exactly the fake closure this project exists to
  prevent.
  `input` now carries the whole conversation - the system item included -
  in the Responses item shape, while `messages` keeps the chat-completions
  shape for strict gateways. Both carry the instruction, so it survives
  whichever dialect the endpoint actually speaks, and nothing has to be
  configured to pick one.

* **`mas doctor` reported a broken setup as healthy.** The handshake only
  proved that the endpoint answers and which dialect it speaks - it could
  not tell whether the instruction it sent was applied. A new
  `instruction` check asks the model to repeat a token that only the
  system instruction mentions, and reports a `warn` with an explicit
  "角色契约可能未生效，闭环判定不可信" when the endpoint answers but the
  instruction was not applied. The probe now also sends the same payload
  shape the provider sends, so the handshake tests the request the runtime
  really makes.

### Added

* `mas.probe.probe_instruction_echo` - the instruction check on its own,
  usable outside `mas doctor`.
* `mas doctor` gained the `instruction` check. It runs on every configured
  non-mock endpoint and is reported by the MCP tool `mas_doctor` as well.
* Regression tests for the dropped-system case, for the provider payload
  shape and for the new check.

## [0.7.18] - 2026-11-14

### Fixed

Two defects in the checks added in 0.7.17. Both passed on Windows and
failed on Linux and macOS.

* The installer test compared the registered `PYTHONPATH` after turning
  every forward slash into a backslash. That is right on Windows and
  wrong on POSIX, where the path has no backslashes to begin with - the
  assertion named a file that cannot exist. The comparison now follows
  the platform.
* The smoke check started the installed runtime through its package path
  without putting the package directory on `sys.path`, so the import of
  `mas` failed with `ModuleNotFoundError`. The runtime is now started the
  way the MCP registration starts it.

## [0.7.17] - 2026-11-14

### Fixed

The one-click installer had three defects that made the "download one script
and run it" promise untrue. All three were found by testing the installer the
way a beginner uses it: with only the script on disk and no source checkout.

* **The installer required a source checkout.** `install.ps1` and `install.sh`
  failed outright when no `mas/` package sat next to them. They now fetch the
  published wheel from the GitHub release and unpack it with the standard
  library, so a single downloaded file is enough - no git, no pip, no
  checkout. `-Version` (or `MAS_VERSION`) pins a specific release.

* **The installer was shadowed by its own working directory.** The runtime was
  started through `python -m mas`, and `python -m` puts the current working
  directory first on `sys.path`. In a checkout that directory contains the
  `mas/` package, so the MCP registration and the agent skills came from the
  checkout while the freshly installed runtime sat unused. The installer now
  registers the runtime by its package path, and the registration points at
  the installed copy explicitly.

* **The MCP registration did not say where the configuration was.** The
  registration carried `PYTHONPATH` but not the configuration location, so a
  host process could start the runtime and still read a different
  `config.json`. Both registrations now carry the configuration path, and the
  runtime resolves it through one helper.

Two further defects were environment-specific but hit real machines:

* The interpreter path was written into the registration as found on `PATH`.
  Under Git Bash that is a POSIX path without an `.exe` suffix, which the
  Windows host cannot execute. The path is now normalised to an absolute
  interpreter path before it is registered.

* `install.sh` separated `PYTHONPATH` entries with a colon. The Windows
  Python it invoked only understands the platform separator, so the installed
  path never reached `sys.path`.

### Added

* `-Wheel` (PowerShell) and `MAS_WHEEL` (shell) install a wheel from disk
  instead of downloading one. The release pipeline and the CI install matrix
  use it to install a build that has not been published yet.
* `tools/verify_install.py` now checks what a user actually receives: that
  `import mas` resolves to the installed runtime rather than to a checkout,
  that the registered command and entry point exist, that both registrations
  point at the installed runtime, and that both carry the configuration
  location.
* `tools/check_install_smoke.py` asserts the version-independent contract of
  an installation, so a published release stays verifiable after the
  registration format moves on.
* `tools/check_installed_version.py` asserts that the installed runtime is
  exactly the build it was handed.
* CI gained a standalone install leg that stages the installer scripts
  alone - no checkout, no package - and installs from them, plus a second leg
  that installs a wheel built from the current checkout and asserts the
  installed version matches the repository.

### Changed

* The runtime is launched through its package path everywhere: the
  installer, the launcher, the MCP registration, and the verifier.
* `pyproject.toml` restricts pytest to `tests/`. The scratch scripts under
  `work/` were being collected as test modules and failed the run on their
  own import errors - a failure in a scratch file, not in the product.

## [0.7.16] - 2026-11-13

### Added

* `mas relay`: the DeepSeek Harness host can drive `mas_run` end to end.
  Verified inside a DSH 0.2.2-rc.3 host - `status=closed`, `iterations=1`,
  `tokens_used=4468`, `run_id=R-9644103b33`.
* DSH plugin `dsh-openloop-mas` for the desktop and web profiles.

## [0.7.15] - 2026-11-13

### Added

* File-level machine checks and the write-file protocol: the closure loop can
  now verify real artefacts on disk instead of only self-reported claims.

## [0.7.14] - 2026-11-12

### Added

* DeepSeek Harness integration. The closure capability is now shared across
  clients rather than living inside one.

## [0.7.13] - 2026-11-12

### Fixed

* An absent reviewer no longer counts as an abstention vote. A missing vote
  was previously indistinguishable from a deliberate abstention, which made
  the verdict wrong.

## [0.7.8] and earlier

See the repository history for the versions before this file was introduced.
