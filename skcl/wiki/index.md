# SKCL 知识库目录
# sgMAP/skcl/wiki/index.md
# 所有 Agent 启动时优先读取此文件，定位所需知识页面
# 更新规则：每次 Ingest 操作后必须更新此文件
# 格式：- [[页面名]] — 一行摘要 (tags)

---

## Skills（技能库）

> 路径：`skcl/skills/`
> 命名规范：`skill_<功能名>.md`

（暂无已注册 Skill，首个 Skill 注册后在此添加条目）

---

## Entities（实体页面）

> 路径：`skcl/wiki/entities/`
> 命名规范：`entity_<名称>.md`

（暂无实体页面）

---

## Concepts（概念页面）

> 路径：`skcl/wiki/concepts/`
> 命名规范：`concept_<名称>.md`

（暂无概念页面）

---

## Queries（任务结论归档）

> 路径：`skcl/wiki/queries/`
> 命名规范：`query_YYYYMMDD_<任务ID>.md`

（暂无已归档结论）

---

## 维护说明

- 每次 Ingest 新内容后，在对应分区追加一行
- 格式：`- [[文件名（不含扩展名）]] — 一句话摘要 (tag1, tag2)`
- Lint 检查：每 50 次 Ingest 后或 ReviewerAgent 触发时，检查孤儿页面和矛盾内容
