---
name: skill_template
version: "1.0"
learned_by: 节点名称
learned_at: YYYY-MM-DD
applicable_roles: [analyzer, coder]
min_memory_gb: 8
requires_gpu: false
tags: [模板, 示例]
---

# Skill: [技能名称]

## 用途

一句话说明这个 Skill 解决什么问题。

## 调用方式

```python
# 示例调用代码
# 描述输入参数和返回值
def run_skill(input_param: str) -> dict:
    """
    Args:
        input_param: 输入说明
    Returns:
        dict: 输出说明
    """
    pass
```

## 依赖

```bash
pip install package1>=1.0 package2>=2.0
# 或
conda install -c bioconda package3
```

硬件要求：
- 内存：≥ X GB
- GPU：是/否（如需要，说明显存要求）

## 执行步骤

1. 步骤一
2. 步骤二
3. 步骤三

## 输出说明

描述输出文件的路径、格式、内容。

## 注意事项

- 已知限制
- 适用场景
- 常见错误及处理方式

## 变更历史

- 1.0（YYYY-MM-DD）：初始版本，由 [节点名] 习得
