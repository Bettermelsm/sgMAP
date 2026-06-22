# AGENTS.md
# sgMAP 节点能力注册表
# 存放路径：sgMAP/skcl/AGENTS.md
# 作用：Hermes Orchestrator 启动时读取，了解集群节点能力分布
# 更新方式：直接编辑此文件，Orchestrator 每 30 分钟自动刷新（无需重启）
# 最后更新：2026-06-16

---

## 节点注册表

| 节点名 | Role | capabilities（精确匹配，与 agent_sdk 注册时一致）| 内存 | GPU |
|--------|------|------------------------------------------------|------|-----|
| SGlcl02 | coder | 代码生成,DeepLabCut,BCI实时,ROS2控制,脚本开发,工作流编排 | 128GB | RTX 2060 12GB |
| SGlcl01 | analyzer | 数据分析,scRNA-seq,WGCNA,分子对接,生信计算,统计分析 | 128GB | RTX 2060S 8GB |
| SGcloud01 | retriever | 全文搜索,GEO下载,数据预处理,STAR比对,FASTQ质控 | 7.6GB | 无 |
| LKcloud01 | evaluator | 质量评估,定时同步,文档生成,SKCL同步,结论验证 | 3.6GB | 无 |
| SGlcl03 | planner | 任务规划,方案设计,Windows专用工具,SPSS分析 | 16GB(WSL2 4GB) | GTX 1660Ti 4GB |

---

## 任务分配规则

### 必须本地执行（禁止路由到远程）
- **BCI 闭环实时处理**：延迟敏感，必须在 SGlcl02 执行
- **InMoov 机器人串口控制**：Arduino 物理连接在 SGlcl02，pyserial 必须本地
- **ROS2 实时节点**：跟随硬件位置，在 SGlcl02

### 内存限制
- SGcloud01（7.6GB）：只能做数据采集、上游比对，**不能做 scRNA-seq 下游分析或 WGCNA**
- LKcloud01（3.6GB）：只能做轻量定时任务，**不能做任何计算密集型任务**
- SGlcl03 WSL2（4GB 上限）：只能做规划类任务和 Windows 专用工具

### 大文件传输规则
- 文件 ≤ 500MB：通过 sgMAP Hub 内置 `/api/files/` 接口中转
- 文件 > 500MB：通过 Syncthing `/data/sga/` 目录同步，在任务 description 中注明路径
- 不得通过 Hub 中转大文件

### 优先级说明
- P0：紧急（系统故障、实时控制异常）
- P1：高（用户主动发起的计算任务）
- P2：普通（定时任务、批量处理）
- P3：低（后台同步、报告生成）

---

## 特殊节点说明

### oracle24G04（sgMAP Hub 节点）
- **不作为 Worker Agent**，专职运行 Hub（main.py）
- 同时运行：sing-box（科学上网）、Hugo 博客、Grafana、Prometheus、Gitea
- 预留内存：约 2GB 用于以上服务，剩余 ~22GB 可用

### MacBook Air × 2
- 作为前端终端，运行 Openclaw 和 Obsidian
- 不注册为 sgMAP Worker Agent
- 通过浏览器访问 Hub 看板（http://oracle24G04_IP:9527）

---

## capabilities 字段维护说明

`capabilities` 字段必须与各节点 `agent_sdk` 注册时声明的完全一致（字符串精确匹配）。
节点注册示例：

```python
agent = AgentClient(
    name="SGlcl01@sengene",
    role="analyzer",
    capabilities=["数据分析", "scRNA-seq", "WGCNA", "分子对接", "生信计算", "统计分析"],
    platform_url="http://<Hub_IP>:9527",
    api_key="your-api-key",
)
```

新增节点时，在本文件的节点注册表中添加一行，并确保 capabilities 与代码一致。
