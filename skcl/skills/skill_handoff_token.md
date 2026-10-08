---
name: skill_handoff_token
version: "1.0"
learned_by: SGlcl03
learned_at: 2026-09-30
applicable_roles: [planner, coder, analyzer, evaluator, retriever]
min_memory_gb: 0
requires_gpu: false
tags: [多智能体, 交接, 人工介入, 协作协议, 流程治理]
---

# Skill: 交接口令字符串（Handoff Token）

## 用途

一句话：给多智能体 + 人工混合协作，提供一套贴在每条消息末尾的**交接状态戳**，和一套只有人能发的**解锁口令**，让人在多个项目/多个 Agent 之间穿插时，扫一眼就知道"球在谁手上、下一步该找谁"，并且防止该停的流程被随口一句"继续"冲过去。

解决两个真实痛点：
1. 人同时盯多个项目、在多个 Agent 之间切换，容易记不清某件事该问谁、进行到哪一步。
2. Agent 容易"一冲到底"，把本应等待人工审核的停止点越过。

## 两种 Token（不要混用）

### A. 交接状态戳 HANDOFF（每条消息末尾必附一行）

每个 Agent（含人工回复，如愿意）在**每次输出的最后一行**附一个状态戳：

```
⟦PROJ·PHASE·ACTOR→NEXT·SEQ·STATE⟧
```

字段（顺序固定，用居中点 `·` 分隔）：

| 字段 | 含义 | 取值示例 |
|------|------|----------|
| PROJ | 项目短码（大写） | GROOM / QPCR / FLW / DLC / BLOG |
| PHASE | 阶段码 | P0 / P1 / P2 … 或 QC / TRAIN / REPORT |
| ACTOR | 刚完成本步的执行者 | KIRO / GLM / USER / 节点名（SGlcl01 等） |
| NEXT | 下一步该谁动（**最关键字段**，箭头指向谁就找谁） | USER / KIRO / GLM / SGlcl02 |
| SEQ | 4 位全局流水号，跨项目递增，便于回溯 | 0007 |
| STATE | 当前状态 | DONE / WAIT / BLOCK / NEED |

STATE 取值定义：

- `DONE` —— 本步干完、无悬念，NEXT 可直接接手
- `WAIT` —— 等待审核/批准才能继续（对应一个停止点）
- `BLOCK` —— 卡住了（报错/缺依赖/缺数据），NEXT 需要先排障
- `NEED` —— 需要 NEXT 提供输入/做决策才能往下

示例：

```
⟦GROOM·P1·GLM→USER·0007·WAIT⟧
```
读作：自我修饰项目、Phase 1、GLM 刚做完、球在 USER、第 7 号、等你审核。

```
⟦GROOM·P2·KIRO→GLM·0008·DONE⟧
```
读作：Kiro 写完 Phase 2 指令、该 GLM 执行、第 8 号、无悬念。

### B. 解锁口令 GO（只有人能发，用于放行停止点）

遇到 STATE=WAIT 的停止点，Agent 不得自行继续。只有**人**发出下面这个精确字符串，对应 Agent 才能过闸：

```
GO PROJ·PHASE→NEXTPHASE
```

示例：`GO GROOM·P1→P2` —— 放行自我修饰项目从 Phase 1 进入 Phase 2。

规则：
- GO 必须项目名 + 阶段精确匹配，Agent 不接受模糊的"继续/往下走"作为过闸依据。
- 人也可发 `HOLD PROJ·PHASE`（叫停）、`REDO PROJ·PHASE`（打回重做）。
- 人有权越过任何停止点，但建议用 GO 显式发令，留下可追溯的决策痕迹。

## 调用方式

这是一套**约定协议**，不是可执行代码。各 Agent 在系统提示/角色设定里内化以下两条规则即可：

```text
规则1（出戳）：你的每一次回复，最后一行必须是一个 HANDOFF 状态戳
            ⟦PROJ·PHASE·ACTOR→NEXT·SEQ·STATE⟧，字段取值见本 Skill。
规则2（守闸）：当你把 STATE 置为 WAIT 时，未收到人工发出的匹配
            GO 口令前，不得继续后续阶段；收到模糊的"继续"也不行。
```

可选的轻量校验（供 Hub/Orchestrator 做格式检查）：

```python
import re

TOKEN_RE = re.compile(
    r"⟦(?P<proj>[A-Z0-9]+)·(?P<phase>[A-Za-z0-9]+)·"
    r"(?P<actor>[A-Za-z0-9]+)→(?P<next>[A-Za-z0-9]+)·"
    r"(?P<seq>\d{4})·(?P<state>DONE|WAIT|BLOCK|NEED)⟧"
)

def parse_handoff(line: str) -> dict | None:
    """解析一行交接状态戳；不匹配返回 None。"""
    m = TOKEN_RE.search(line.strip())
    return m.groupdict() if m else None

GO_RE = re.compile(r"^GO\s+(?P<proj>[A-Z0-9]+)·(?P<from>[A-Za-z0-9]+)→(?P<to>[A-Za-z0-9]+)$")

def is_go_for(cmd: str, proj: str, phase: str) -> bool:
    """判断人发的 GO 口令是否放行指定项目的指定阶段。"""
    m = GO_RE.match(cmd.strip())
    return bool(m and m["proj"] == proj and m["from"] == phase)
```

## 依赖

无。纯文本约定；可选校验仅用 Python 标准库 `re`。

硬件要求：
- 内存：无要求
- GPU：否

## 执行步骤

1. 为项目起一个短码（PROJ），全大写，团队内唯一。
2. 把本 Skill 的规则1、规则2 写进每个参与 Agent 的角色设定/系统提示。
3. 每个 Agent 回复时在末尾出 HANDOFF 戳；SEQ 由发起方统一递增（或由 Hub 统一派号）。
4. 人在停止点用 GO/HOLD/REDO 口令发令；Agent 守闸。
5. （可选）Hub/Orchestrator 用上面的正则对消息做格式校验与路由，NEXT 字段可直接驱动"下一步派给谁"。

## 输出说明

无文件产出。产物是嵌入每条消息末尾的一行文本戳，以及人工发出的 GO/HOLD/REDO 口令行。

## 注意事项

- `·` 是居中点 U+00B7，`→` 是箭头 U+2192，`⟦⟧` 是 U+27E6/U+27E7；复制时注意不要替换成普通点号或 ->。若某终端输入不便，允许退化写法 `[[PROJ|PHASE|ACTOR>NEXT|SEQ|STATE]]`，但同一项目内保持统一。
- SEQ 建议全局递增而非每项目独立，这样跨项目回溯时序更清楚；多 Agent 并发时由 Hub 统一派号避免撞号。
- 守闸只对 STATE=WAIT 生效；DONE/NEED/BLOCK 不是闸，但 NEED/BLOCK 意味着 NEXT 必须先响应。
- 这是协作治理约定，不替代各 Skill 自身的安全红线（如"原始数据只读""不自行装软件"）。
- 常见错误：Agent 出了 WAIT 戳却继续执行 —— 属于违反规则2，应在角色设定里强调守闸优先级高于"尽量多做"。

## 变更历史

- 1.0（2026-09-30）：初始版本。源于自我修饰行为分析项目中 Kiro/GLM/人工三方穿插协作时的交接混乱，抽象为通用协议，由 SGlcl03 习得。