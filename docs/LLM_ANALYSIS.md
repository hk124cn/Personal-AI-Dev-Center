# LLM 智能分析功能

## 概述

Dev Center 现在支持使用大语言模型（LLM）对项目 MD 文档进行智能语义分析，自动提取：
- **真正的问题**（bugs、issues）
- **待办事项**（todos）
- **已完成功能**（features）
- **技术架构**（architecture）
- **API 路由**（routes）
- **项目摘要**（summary）

## 配置方法

### 1. 编辑 config.json

在 `config.json` 中添加或修改 `llm` 配置部分：

```json
{
  "llm": {
    "enabled": true,
    "provider": "商汤科技",
    "api_key": "sk-xxxx",
    "model": "sensenova-6.7-flash-lite",
    "base_url": "https://token.sensenova.cn/v1"
  },
  ...
}
```

### 2. 支持的 LLM 提供商

> 当前项目默认配置为 **商汤 SenseNova**（走 OpenAI 兼容接口）。后端按 `provider` 分流：`anthropic` → Anthropic 原生接口；`qwen` → 通义千问；其它值（含 `商汤科技`）→ OpenAI 兼容接口（需填 `base_url`）。

#### 商汤 SenseNova（当前默认配置）
```json
{
  "provider": "商汤科技",
  "api_key": "sk-...",
  "model": "sensenova-6.7-flash-lite",
  "base_url": "https://token.sensenova.cn/v1"
}
```

#### Anthropic Claude（备选）
```json
{
  "provider": "anthropic",
  "api_key": "sk-ant-api03-...",
  "model": "claude-3-5-sonnet-20241022"
}
```

可用模型：
- `claude-3-5-sonnet-20241022`（推荐）
- `claude-3-opus-20240229`
- `claude-3-haiku-20240307`

#### OpenAI
```json
{
  "provider": "openai",
  "api_key": "sk-...",
  "model": "gpt-4-turbo-preview"
}
```

可用模型：
- `gpt-4-turbo-preview`
- `gpt-4o`
- `gpt-3.5-turbo`

#### 通义千问（阿里云）
```json
{
  "provider": "qwen",
  "api_key": "sk-...",
  "model": "qwen-max"
}
```

可用模型：
- `qwen-max`
- `qwen-plus`
- `qwen-turbo`

### 3. 获取 API Key

#### 商汤 SenseNova
访问 https://console.sensenova.cn/ 或 https://token.sensenova.cn/ 注册并获取 API key（别忘了在 config 里填 `base_url`）

#### Anthropic
访问 https://console.anthropic.com/ 注册并获取 API key

#### OpenAI
访问 https://platform.openai.com/api-keys 获取 API key

#### 通义千问
访问 https://dashscope.aliyun.com/ 注册并获取 API key

## 使用方法

### 启用 LLM 分析

1. 在 `config.json` 中设置 `"enabled": true`
2. 填入正确的 `api_key`
3. 运行同步命令：
   ```bash
   python backend/sync.py
   ```

### 查看分析结果

同步完成后，`backend/data/latest.json` 中每个项目会包含以下新字段：

```json
{
  "id": "ecommerce-platform",
  "name": "DEMO-PROJECT",
  ...
  "llm_features": [
    "多因子评分系统",
    "财报评分网页",
    "个股预警功能"
  ],
  "llm_architecture": {
    "modules": [
      "src/core/base_factor.py",
      "src/factors/attention_factor.py"
    ],
    "tech_stack": ["Python", "Flask"]
  },
  "llm_routes": [
    {
      "path": "/api/stocks",
      "method": "GET",
      "description": "获取股票列表"
    }
  ],
  "llm_summary": "自动化股票评分系统，已完成核心评分引擎和多个前端页面，待优化回测逻辑和自动交易功能。",
  "llm_analyzed": true
}
```

### 禁用 LLM 分析

如果不想使用 LLM 分析，只需将 `enabled` 设为 `false`：

```json
{
  "llm": {
    "enabled": false,
    ...
  }
}
```

同步时会自动跳过 LLM 分析步骤，仅使用传统的规则解析。

## 注意事项

### Token 消耗

- 每个项目的 MD 文档内容会被截断到最多 3000 字符
- 一次完整同步（13 个项目）大约消耗 5000-8000 tokens
- 建议定期同步而非实时同步，以控制成本

### 性能影响

- LLM 分析会增加同步时间（每个项目约 2-5 秒）
- 首次全量同步可能需要额外 30-60 秒
- 可以只同步特定项目来减少等待时间：
  ```bash
  python -c "from backend.sync import sync_single; sync_single('ecommerce-platform')"
  ```

### 隐私安全

- API key 存储在本地 `config.json` 文件中
- 不会上传代码文件内容，只发送 MD 文档文本
- 建议不要将包含 API key 的配置文件提交到版本控制系统

## 故障排除

### LLM 分析未生效

检查以下几点：
1. `enabled` 是否设置为 `true`
2. `api_key` 是否正确填写
3. 网络连接是否正常
4. 查看控制台输出是否有 `[LLM]` 开头的错误信息

### API 调用失败

常见错误及解决方法：

**Authentication Error**
- 检查 API key 是否正确
- 确认账户余额充足

**Rate Limit Exceeded**
- 降低同步频率
- 联系 LLM 提供商提高配额

**Timeout**
- 检查网络连接
- 尝试更换模型（选择更快的模型）

## 示例配置

参考 `config.llm.example.json` 文件，其中包含了完整的 LLM 配置示例。

## 未来计划

- [ ] 支持更多 LLM 提供商（Google Gemini、DeepSeek 等）
- [ ] 增量分析（只分析变更的文件）
- [ ] 缓存分析结果，避免重复调用
- [ ] 自定义分析模板
- [ ] 批量重新分析已有项目
