---
name: mas-review
description: 多视角对抗式审查（代码、方案、稿件都可以），当用户要求"审一下 / 挑毛病 / 找漏洞 / 交叉验证 / 多 agent 审查 / 质量门"，或需要在交付前跑一轮独立验证并留下可追溯记录时使用。基于 OpenLoop MAS 的闭环引擎：执行者产出初稿，验证者独立挑错，复盘者给修复清单。
---

# 对抗式审查（OpenLoop MAS · review 团队）

## 审查闭环

1. **planner** 列出本次审查必须覆盖的维度（事实、结构、逻辑、风险、缺漏）。
2. **worker** 产出被审对象本身（代码、方案、稿件），不改写、不自我美化。
3. **verifier** 独立判定：默认怀疑，没有原文证据的结论一律不通过。
4. **reflector** 输出未通过项 + 最小修复动作。
5. **lead** 判断是否可以放行；不能放行就**升级人工工单**，不硬凑结论。

## 关键约束

- 审查者与被审者**不是同一个上下文**：verifier 只看产物与验收标准，不看 worker 的自述理由。
- 判定必须带证据（引用原文片段或具体行号），无证据的"我觉得没问题"不算通过。
- 审查结论写进账本（`mas ledger`），可回看、可复现、可交接。

## 用法

```bash
# 用 review 团队跑一次审查
mas run --team review --goal "审查这份设计方案" \
  --criteria '{"id":"c1","desc":"每个结论都有证据","check":{"type":"contains","arg":"证据"}}' \
  --criteria '{"id":"c2","desc":"列出至少三条风险","check":{"type":"lines_min","arg":3}}'

# 人工介入
mas tickets
mas resume <ticket_id> --note "只看 P0 问题，其余先放着"
```


## v0.2 增强

- **并行候选**：被审对象可以并发产出多版（`--parallel 3 --perspective thorough`），
  引擎按机检通过数确定性选优，避免"审到哪版算哪版"。
- **多评审人格投票**：`strict` / `skeptic` / `completeness` / `reader`，
  `--review-mode majority` 可降低单一评审的误判。
- **证据留痕**：审查结论写进账本，可用 `mas report` / `mas replay <run_id>` 回看每一步，
  交接与复盘不靠口头复述。

```bash
mas run --team review --goal "审查这份设计方案" --criteria-file criteria.json \
  --parallel 2 --reviewers 3 --review-mode majority
mas replay <run_id>
```

## 退出码

`mas run` 的退出码：`0` = 闭合（全部标准通过），`2` = 未闭合（升级或预算耗尽）。
CI 里可以直接用退出码当门禁。
