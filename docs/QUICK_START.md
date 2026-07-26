# 快速开始 - LLM 智能分析设置

## 30 秒配置指南

### 步骤 1: 点击设置按钮 ⚙️

![Step 1](https://via.placeholder.com/600x100?text=Click+Settings+Button)

在页面右上角找到齿轮图标，点击它。

### 步骤 2: 填入 API Key

![Step 2](https://via.placeholder.com/600x300?text=Enter+API+Key)

1. ✅ 勾选 **"启用 LLM 智能分析"**
2. 🔧 选择提供商（推荐 **商汤 SenseNova**，当前默认配置）
3. 🔑 粘贴你的 API key（商汤：https://console.sensenova.cn/）
4. 🤖 选择模型（推荐 **sensenova-6.7-flash-lite**）

### 步骤 3: 保存并同步

![Step 3](https://via.placeholder.com/600x100?text=Save+and+Sync)

1. 点击 **"💾 保存配置"**
2. 看到提示 "✓ LLM 配置已保存"
3. 点击 **"🔄 同步"** 按钮开始同步

### 完成！✨

同步完成后，查看 `backend/data/latest.json` 中的 `llm_*` 字段，即可看到智能分析结果！

---

## 获取 API Key

### 商汤 SenseNova（当前默认配置）
1. 访问 https://console.sensenova.cn/ 或 https://token.sensenova.cn/
2. 注册/登录账户
3. 创建新的 API key
4. 复制 key 到设置框（注意在 config 中填 `base_url`）

### Anthropic Claude（备选）
1. 访问 https://console.anthropic.com/
2. 注册/登录账户
3. 创建新的 API key
4. 复制 key 到设置框

### OpenAI GPT
1. 访问 https://platform.openai.com/api-keys
2. 创建新的 secret key
3. 复制 key 到设置框

### 通义千问
1. 访问 https://dashscope.aliyun.com/
2. 开通 DashScope 服务
3. 创建 API key
4. 复制 key 到设置框

---

## 验证是否成功

### 方法 1: 查看同步输出
```bash
python backend/sync.py
```

看到 `+LLM` 标记表示成功：
```
[sync] DEMO-PROJECT @ 演示服务器 (YOUR_SERVER_IP)...
  -> OK +LLM (6.52s)  ← 这里有 +LLM
```

### 方法 2: 查看 JSON 文件
打开 `backend/data/latest.json`，查找：
```json
{
  "llm_analyzed": true,  ← 这个为 true
  "llm_summary": "...",   ← 有内容
  "llm_features": [...]   ← 有内容
}
```

---

## 常见问题速查

| 问题 | 解决方法 |
|------|---------|
| 设置按钮没反应 | 检查后端 API 是否运行 |
| 保存失败 | 检查 config.json 权限 |
| 同步太慢 | 使用更快的模型或降低频率 |
| 费用太高 | 使用更便宜的模型或缓存结果 |

---

**需要更多帮助？**  
查看 [完整使用指南](docs/USAGE_GUIDE.md) 或 [LLM 设置 UI 文档](docs/LLM_SETTINGS_UI.md)
