# 清理旧版自检留在账本里的行

> 适用版本：v0.7.92 及以后。
> 触发条件：你在 v0.7.92 之前跑过 `mas selftest`，并且
> `mas report` 报出了**你不认识的审计问题**。

## 一句话说清发生了什么

`mas selftest` 要证明「撤销真的能把文件退回去」，
所以它必须**真的批准一次草稿、真的撤销一次**。
而「批准」是一次**人工决定** —— 人工决定要记进**主账本**。

在 v0.7.92 之前，自检没有把状态目录隔离开，
于是每跑一次自检，你的
`~/.mas/ledgers/runs.jsonl` 就会多出几行，
指向一个**当场就被删掉的临时目录**。

行留下了，链没了。于是 `mas report` 会报出
**几百个审计问题** —— 而那几百个「批准」**是测试自己批的**。

v0.7.92 已经**不再产生**这些行。
这一页讲的是**修复**：把你机器上已经留下的旧行清掉。

## 先确认是不是这个问题

```bash
mas report
```

如果报告里有这样的条目：

```
- 审计链复核：0 条链、0 条记录、458 个问题
```

并且你**没有**真的做过那些批准 —— 那是旧版自检留下的。

也可以直接干跑一次看看会删多少（**不会改任何东西**）：

```bash
mas clean --selftest-ledger --dry-run
```

## 一键清理

```bash
mas clean --selftest-ledger
```

它会：

1. 先把原账本复制成 `runs.jsonl.bak-<时间戳>`；
2. **只**删除同时满足两个条件的行：
   - 事件是 `draft_approve` 或 `draft_undo`；
   - 工作区指向一个临时目录（`Temp` / `tmp` / `mas-audit-*`）。
3. 其余行**一行不动**。

> 任何真实工作区里的批准记录都会保留。
> 读不懂的行也保留 —— 读不懂的东西不允许被销毁。

清完再跑一次：

```bash
mas report
```

## 手工做法（不想装新版本时）

如果你暂时还在用 v0.7.91 或更早，可以自己处理。

**第一步：备份。**

```powershell
Copy-Item "$HOME\.mas\ledgers\runs.jsonl" "$HOME\.mas\ledgers\runs.jsonl.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

**第二步：干跑，数一数会删多少行。**

```powershell
python -c "import json,pathlib,os;p=pathlib.Path(os.path.expanduser('~/.mas/ledgers/runs.jsonl'));rows=[l for l in p.read_text(encoding='utf-8').splitlines() if l.strip()];bad=[r for r in rows if (lambda d: d.get('event') or d.get('role')) in ('draft_approve','draft_undo') and ('\\Temp\\' in str(d.get('workspace') or '') or '\\tmp\\' in str(d.get('workspace') or '')))(json.loads(r))];print('会删',len(bad),'行，共',len(rows),'行')"
```

**第三步：确认数字合理，再执行清理。**

```powershell
python -c "import json,pathlib,os,shutil,datetime;p=pathlib.Path(os.path.expanduser('~/.mas/ledgers/runs.jsonl'));shutil.copy2(p, p.with_name('runs.jsonl.bak-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')));rows=[l for l in p.read_text(encoding='utf-8').splitlines(keepends=True) if l.strip()];keep=[l for l in rows if not (lambda d: (d.get('event') or d.get('role')) in ('draft_approve','draft_undo') and ('\\Temp\\' in str(d.get('workspace') or '') or '\\tmp\\' in str(d.get('workspace') or '')))(json.loads(l))];p.write_text(''.join(keep),encoding='utf-8');print('保留',len(keep),'行')"
```

## 手工做法（跨平台）

如果上面的 PowerShell 转义让你头晕，用 Python 更稳：

```python
import datetime
import json
import os
import pathlib
import shutil

ledger = pathlib.Path(os.path.expanduser("~/.mas/ledgers/runs.jsonl"))
backup = ledger.with_name(
    "runs.jsonl.bak-%s" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
)
shutil.copy2(ledger, backup)

keep = []
dropped = 0
for line in ledger.read_text(encoding="utf-8").splitlines(keepends=True):
    if not line.strip():
        continue
    try:
        row = json.loads(line)
    except ValueError:
        keep.append(line)      # 读不懂就留下
        continue
    event = row.get("event") or row.get("role")
    workspace = str(row.get("workspace") or "")
    temporary = "\\Temp\\" in workspace or "\\tmp\\" in workspace
    if event in ("draft_approve", "draft_undo") and temporary:
        dropped += 1
        continue
    keep.append(line)

ledger.write_text("".join(keep), encoding="utf-8")
print("删除", dropped, "行，保留", len(keep), "行；备份在", backup)
```

## 怎么知道自己该清理

`mas clean --selftest-ledger --json` 会给一份机器可读的结果：

```json
{
  "dropped_count": 493,
  "kept_count": 202,
  "backup": "C:\\Users\\you\\.mas\\ledgers\\runs.jsonl.bak-20261119-202636",
  "written": true
}
```

- `dropped_count` 是被删掉的行数；
- `kept_count` 是留下的行数；
- `backup` 是备份路径 —— **反悔的时候把它改回原名即可**。

## 反悔

把备份改回原名就行：

```powershell
Copy-Item "$HOME\.mas\ledgers\runs.jsonl.bak-<时间戳>" "$HOME\.mas\ledgers\runs.jsonl" -Force
```

## 相关

- 第十个不变量：自检不得污染被它检查的那台机器
  —— 见 [loop-engineering.md](loop-engineering.md)
- 自检命令说明 —— 见 [quickstart.md](quickstart.md) 第 4.11 节
