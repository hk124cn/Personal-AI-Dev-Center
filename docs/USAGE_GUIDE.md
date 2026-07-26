# Dev Center 使用指南

## 快速开始

### 1. 基础同步（不使用 LLM）

```bash
# 进入项目目录
cd "D:/app/Personal AI Dev Center"

# 运行同步命令
python backend/sync.py
```

同步完成后，数据保存在 `backend/data/latest.json`

### 2. 启用 LLM 智能分析（可选）

#### 步骤 1: 配置 API Key

编辑 `config.json`，添加 LLM 配置：

```json
{
  "llm": {
    "enabled": true,
    "provider": "anthropic",
    "api_key": "sk-ant-api03-YOUR_API_KEY_HERE",
    "model": "claude-3-5-sonnet-20241022"
  },
  ...
}
```

**获取 API Key:**
- Anthropic Claude: https://console.anthropic.com/
- OpenAI: https://platform.openai.com/api-keys
- 通义千问: https://dashscope.aliyun.com/

#### 步骤 2: 测试 LLM 连接

```bash
python backend/test_llm.py
```

如果显示 "✓ 所有测试通过！"，说明配置正确。

#### 步骤 3: 运行完整同步

```bash
python backend/sync.py
```

同步输出会显示哪些项目使用了 LLM 分析：
```
[sync] DEMO-PROJECT @ 演示服务器 (YOUR_SERVER_IP)...
  -> OK +LLM (4.01s)
```

## 功能对比

### 传统规则解析 vs LLM 智能分析

| 特性 | 规则解析 | LLM 分析 |
|------|---------|---------|
| **速度** |  快（~2s/项目） | 🐢 较慢（~5s/项目） |
| **准确性** | 一般（基于格式） | 🎯 高（语义理解） |
| **成本** | 免费 | 💰 需要 API 调用 |
| **适用场景** | 标准格式的 MD 文件 | 任意格式的文档 |
| **提取内容** | TODO、进度、问题 | + 功能、架构、路由、摘要 |

### 示例输出对比

#### 规则解析结果
```json
{
  "issues": {
    "current": [
      "**基类**: src/core/base_factor.py",  // ❌ 误识别为问题
      "**因子发现**: src/core/factor_manager.py"
    ]
  }
}
```

#### LLM 分析结果
```json
{
  "issues": {
    "current": [
      "数据更新延迟较大",  // ✓ 真正的问题
      "部分因子计算耗时过长"
    ]
  },
  "llm_features": [
    "多因子评分系统",
    "财报评分网页"
  ],
  "llm_architecture": {
    "modules": ["src/core/base_factor.py"],
    "tech_stack": ["Python", "Flask"]
  },
  "llm_summary": "自动化股票评分系统，已完成核心评分引擎..."
}
```

## 常见问题

### Q1: LLM 分析太慢怎么办？

**A:** 可以只同步特定项目：
```bash
# 修改 sync.py，只同步单个项目
python -c "from backend.sync import sync_single; sync_single('ecommerce-platform')"
```

或者禁用 LLM，只使用规则解析：
```json
{
  "llm": {
    "enabled": false
  }
}
```

### Q2: API 费用太高怎么办？

**A:** 
1. 降低同步频率（每天 1 次而非每小时）
2. 只对重点项目启用 LLM 分析
3. 使用更便宜的模型（如 Claude Haiku）
4. 缓存分析结果，避免重复调用

### Q3: 如何查看某个项目的详细分析结果？

**A:** 打开 `backend/data/latest.json`，找到对应项目：
```json
{
  "id": "ecommerce-platform",
  "name": "DEMO-PROJECT",
  "llm_analyzed": true,
  "llm_summary": "...",
  "llm_features": [...],
  "llm_architecture": {...},
  "llm_routes": [...]
}
```

### Q4: 同步后数据没有变化？

**A:** 检查以下几点：
1. SSH 密钥是否正确配置
2. 服务器是否在线
3. 项目路径是否正确（已在 v2 修复）
4. 远程服务器上是否有 MD 文件

## 高级用法

### 自定义 LLM 提供商

编辑 `backend/llm_analyzer.py`，添加新的提供商支持：

```python
def _call_custom_llm(prompt: str, api_key: str, model: str) -> Optional[dict]:
    """调用自定义 LLM API"""
    import requests
    
    url = "YOUR_API_ENDPOINT"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"prompt": prompt, "model": model}
    
    response = requests.post(url, headers=headers, json=payload)
    result = response.json()
    
    return _extract_json_from_text(result["content"])
```

然后在 `call_llm_api` 函数中注册：
```python
elif provider == "custom":
    return _call_custom_llm(prompt, api_key, model)
```

### 批量重新分析已有项目

创建脚本 `backend/reanalyze.py`：

```python
import json
from pathlib import Path
from llm_analyzer import analyze_project

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "backend" / "data" / "latest.json"
CONFIG_PATH = BASE_DIR / "config.json"

def reanalyze_all():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    llm_config = config.get("llm", {})
    
    for project in data["projects"]:
        if not project.get("md_files"):
            continue
        
        print(f"Reanalyzing: {project['name']}...")
        
        # 这里需要从 SSH 重新读取内容，或使用缓存
        # 简化版：跳过
        
    print("Done!")

if __name__ == "__main__":
    reanalyze_all()
```

## 文件结构

```
Personal AI Dev Center/
├── config.json                    # 主配置文件
├── config.llm.example.json        # LLM 配置示例
├── backend/
│   ├── sync.py                    # 同步引擎（已修复路径问题）
│   ├── llm_analyzer.py           # LLM 分析模块（新增）
│   ├── test_llm.py               # LLM 测试脚本（新增）
│   ── data/
│       └── latest.json           # 同步结果
└── docs/
    ├── LLM_ANALYSIS.md           # LLM 功能文档（新增）
    └── USAGE_GUIDE.md            # 本文件（新增）
```

## 更新日志

### v2.1 (2026-06-14)
- ✅ 修复路径拼接错误（`remote_path` 直接使用绝对路径）
- ✅ 新增 LLM 智能分析功能
- ✅ 支持 Anthropic、OpenAI、通义千问
- ✅ 自动提取功能、架构、路由、摘要
- ✅ 添加测试脚本和文档

### v2.0 (2026-06-14)
- ✅ 自动发现项目目录下所有 MD 文件
- ✅ 通用解析器支持任意 MD 格式
- ✅ 修复 SSH 路径问题

### v1.0 (初始版本)
- ✅ 基础 SSH 同步功能
- ✅ 解析 TODO.md、PROGRESS.md、ISSUES.md

## 支持与反馈

如有问题或建议，请：
1. 查看文档：`docs/LLM_ANALYSIS.md`
2. 运行测试：`python backend/test_llm.py`
3. 检查日志：同步时的控制台输出

---

**祝使用愉快！** 🚀
