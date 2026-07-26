# LLM 智能分析 - Web UI 设置指南

## 快速开始

### 1. 打开 Dev Center 前端

在浏览器中访问：`http://localhost:8080`

### 2. 点击设置按钮

在页面右上角的顶栏，找到 **️ 齿轮图标**（位于"同步"按钮右侧），点击它。

![设置按钮位置](https://via.placeholder.com/400x60?text=Settings+Button)

### 3. 配置 LLM 参数

弹出的设置窗口包含以下选项：

#### ✅ 启用 LLM 智能分析
- 勾选此复选框以启用自动分析功能
- 启用后，每次同步都会调用 LLM 分析项目 MD 文档

#### 🔧 LLM 提供商选择
支持以下提供商：
- **Anthropic Claude**（推荐）- 最准确的理解能力
- **OpenAI GPT** - 广泛兼容
- **通义千问**（阿里云）- 中文优化

####  API Key
输入你的 API key：
- Anthropic: `sk-ant-api03-...`
- OpenAI: `sk-...`
- 通义千问: `sk-...`

点击下方的链接可以快速跳转到对应平台的控制台获取 API key。

#### 🤖 模型选择
根据选择的提供商，会显示对应的可用模型：

**Anthropic:**
- Claude 3.5 Sonnet（推荐）- 最佳平衡
- Claude 3 Opus - 最强但较慢
- Claude 3 Haiku - 更快更便宜

**OpenAI:**
- GPT-4 Turbo
- GPT-4o
- GPT-3.5 Turbo（更便宜）

**通义千问:**
- qwen-max
- qwen-plus
- qwen-turbo（更便宜）

### 4. 保存配置

点击 **" 保存配置"** 按钮，系统会：
1. 将配置保存到 `config.json`
2. 显示成功提示："✓ LLM 配置已保存"
3. 关闭设置窗口

如果保存失败，会显示错误信息。

## 使用效果

### 启用前
同步输出：
```
[sync] DEMO-PROJECT @ 演示服务器 (YOUR_SERVER_IP)...
  -> OK (4.01s)
```

### 启用后
同步输出：
```
[sync] DEMO-PROJECT @ 演示服务器 (YOUR_SERVER_IP)...
  -> OK +LLM (6.52s)
```

注意 `+LLM` 标记，表示该项目使用了 LLM 分析。

## 查看分析结果

同步完成后，可以在以下位置查看 LLM 分析的结果：

### 1. JSON 数据文件
打开 `backend/data/latest.json`，每个项目包含：

```json
{
  "id": "ecommerce-platform",
  "name": "DEMO-PROJECT",
  ...
  "llm_analyzed": true,
  "llm_summary": "自动化股票评分系统，已完成核心评分引擎...",
  "llm_features": [
    "多因子评分系统",
    "财报评分网页",
    "个股预警功能"
  ],
  "llm_architecture": {
    "modules": ["src/core/base_factor.py"],
    "tech_stack": ["Python", "Flask"]
  },
  "llm_routes": [
    {"path": "/api/stocks", "method": "GET", "description": "获取股票列表"}
  ]
}
```

### 2. 前端展示（未来计划）
后续版本将在前端直接展示 LLM 分析的结果，包括：
- 项目摘要卡片
- 功能特性列表
- 技术架构图
- API 路由表

## 禁用 LLM 分析

如果不想继续使用 LLM 分析：

1. 点击 ⚙️ 设置按钮
2. 取消勾选 **"启用 LLM 智能分析"**
3. 点击 **"保存配置"**

同步时将跳过 LLM 分析步骤，仅使用传统的规则解析。

## 费用估算

### Token 消耗
- 每个项目约 500-800 tokens（取决于 MD 文档大小）
- 13 个项目全量同步约 6500-10400 tokens

### 成本参考（Claude 3.5 Sonnet）
- 输入：$3 / 百万 tokens
- 输出：$15 / 百万 tokens
- 单次全量同步：约 $0.10 - $0.15 USD

### 节省建议
1. **降低同步频率**：每天 1 次而非每小时
2. **只对重点项目启用**：可以手动选择哪些项目使用 LLM
3. **使用更便宜的模型**：如 Claude Haiku 或 GPT-3.5
4. **缓存分析结果**：避免重复分析未变更的项目

## 故障排除

### Q: 点击设置按钮没反应？
**A:** 
1. 检查后端 API 是否运行：`python backend/app.py`
2. 检查浏览器控制台是否有错误信息
3. 刷新页面重试

### Q: 保存配置时提示"保存失败"？
**A:**
1. 检查后端 API 是否正常运行
2. 确认 `config.json` 文件有写入权限
3. 查看后端日志中的错误信息

### Q: 启用后同步变慢了？
**A:** 
这是正常的，LLM 分析需要额外时间（每个项目约 2-5 秒）。可以通过以下方式优化：
1. 使用更快的模型（如 Claude Haiku）
2. 只同步特定项目
3. 在非工作时间进行全量同步

### Q: API Key 安全吗？
**A:**
- API Key 存储在本地 `config.json` 文件中
- 不会上传到任何外部服务器（除了发送给 LLM 提供商）
- 建议不要将包含 API Key 的配置文件提交到 Git

## 高级用法

### 通过 API 直接配置

你也可以直接编辑 `config.json` 文件来配置 LLM：

```json
{
  "servers": [...],
  "projects": [...],
  "llm": {
    "enabled": true,
    "provider": "anthropic",
    "api_key": "sk-ant-api03-YOUR_KEY",
    "model": "claude-3-5-sonnet-20241022"
  }
}
```

然后重启后端服务即可生效。

### 自定义 LLM 提供商

如果需要支持其他 LLM 提供商（如 Google Gemini、DeepSeek 等）：

1. 编辑 `backend/llm_analyzer.py`，添加新的调用函数
2. 在前端 `index.html` 的 `getModelOptions()` 中添加新选项
3. 重启服务

## 下一步

- [ ] 前端直接展示 LLM 分析结果
- [ ] 增量分析（只分析变更的文件）
- [ ] 分析历史对比
- [ ] 批量重新分析已有项目

---

**祝使用愉快！** 🚀

如有问题，请查看：
- [完整使用指南](USAGE_GUIDE.md)
- [LLM 功能文档](LLM_ANALYSIS.md)
