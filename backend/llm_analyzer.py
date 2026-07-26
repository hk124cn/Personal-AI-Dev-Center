"""
Dev Center - LLM Analyzer Module
使用大模型智能分析项目 MD 文档内容，提取真正的问题、待办、功能等信息
"""

import json
import os
import sys
from typing import Optional, Dict, Any

# Windows 控制台 GBK 编码无法打印 emoji 等 Unicode 字符，强制使用 UTF-8
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def call_llm_api(prompt: str, config: dict) -> Optional[dict]:
    """
    调用 LLM API 进行分析
    
    Args:
        prompt: 发送给 LLM 的提示词
        config: LLM 配置字典
        
    Returns:
        解析后的 JSON 结果，失败返回 None
    """
    provider = config.get("provider", "anthropic")
    api_key = config.get("api_key", "")
    model = config.get("model", "claude-3-5-sonnet-20241022")
    base_url = config.get("base_url", "")
    
    if not api_key:
        print("[LLM] Warning: No API key configured, skipping LLM analysis")
        return None
    
    print(f"\n[LLM] === 请求开始 ===")
    print(f"[LLM] 提供商: {provider} | 模型: {model} | Base URL: {base_url or '(default)'}")
    print(f"[LLM] Prompt 长度: {len(prompt)} 字符")
    print(f"[LLM] Prompt 内容:")
    print("-" * 60)
    # 打印 prompt，截断到 2000 字符
    if len(prompt) > 2000:
        print(prompt[:2000])
        print(f"... (截断，共 {len(prompt)} 字符)")
    else:
        print(prompt)
    print("-" * 60)
    
    try:
        if provider == "anthropic":
            result = _call_anthropic(prompt, api_key, model)
        elif provider == "qwen":
            result = _call_qwen(prompt, api_key, model)
        else:
            result = _call_openai(prompt, api_key, model, base_url)
        
        print(f"[LLM] 返回结果:")
        print("-" * 60)
        if result:
            result_str = json.dumps(result, ensure_ascii=False, indent=2)
            if len(result_str) > 3000:
                print(result_str[:3000])
                print(f"... (截断，共 {len(result_str)} 字符)")
            else:
                print(result_str)
        else:
            print("(解析失败，无有效 JSON)")
        print("-" * 60)
        print(f"[LLM] === 请求结束 ===\n")
        return result
    except Exception as e:
        print(f"[LLM] Error calling API: {str(e)}")
        print(f"[LLM] === 请求失败 ===\n")
        return None


def _call_anthropic(prompt: str, api_key: str, model: str) -> Optional[dict]:
    """调用 Anthropic Claude API"""
    import requests
    
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    payload = {
        "model": model,
        "max_tokens": 2000,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    
    result = response.json()
    content = result["content"][0]["text"]
    
    # 尝试从响应中提取 JSON
    return _extract_json_from_text(content)


def _call_openai(prompt: str, api_key: str, model: str, base_url: Optional[str] = None) -> Optional[dict]:
    """调用 OpenAI 兼容 API"""
    import requests

    url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    
    result = response.json()
    content = result["choices"][0]["message"]["content"]
    
    return _extract_json_from_text(content)


def _call_qwen(prompt: str, api_key: str, model: str) -> Optional[dict]:
    """调用通义千问 API"""
    import requests
    
    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "input": {
            "messages": [
                {"role": "user", "content": prompt}
            ]
        },
        "parameters": {
            "result_format": "text"
        }
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    
    result = response.json()
    content = result["output"]["text"]
    
    return _extract_json_from_text(content)


def _extract_json_from_text(text: str) -> Optional[dict]:
    """从文本中提取 JSON 对象"""
    import re
    
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 尝试查找 JSON 代码块
    json_match = re.search(r'```json\s*([\s\S]*?)\s*```', text)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    
    # 尝试查找花括号包裹的内容
    brace_match = re.search(r'\{[\s\S]*\}', text)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass
    
    print(f"[LLM] Failed to extract JSON from response: {text[:200]}...")
    return None


def build_analysis_prompt(project_name: str, md_contents: Dict[str, str]) -> str:
    """
    构建 LLM 分析提示词
    
    Args:
        project_name: 项目名称
        md_contents: MD 文件内容字典 {filename: content}
        
    Returns:
        完整的提示词字符串
    """
    # 限制每个文件的内容长度，避免 token 过多
    max_content_length = 3000
    
    files_summary = []
    for filename, content in md_contents.items():
        if content is None:
            continue
        truncated = content[:max_content_length]
        if len(content) > max_content_length:
            truncated += "\n...(内容已截断)"
        files_summary.append(f"\n=== {filename} ===\n{truncated}")
    
    all_content = "\n".join(files_summary)
    
    prompt = f"""你是一个软件项目管理专家。请分析以下项目的 Markdown 文档内容，并提取结构化信息。

项目名称：{project_name}

文档内容：
{all_content}

请仔细分析这些文档，区分：
1. **真正的问题**（bugs、issues、需要修复的错误）
2. **待办事项**（todos、计划要做的事情、未完成的功能）
3. **已完成功能**（已经实现的功能特性）
4. **技术架构**（代码结构、模块说明、文件路径等）
5. **API路由**（URL端点、接口定义等）

重要：不要把代码文件路径、功能列表、技术说明当成"问题"或"待办"。

请以 JSON 格式返回分析结果，格式如下：

{{
  "issues": {{
    "current": ["当前存在的问题1", "问题2"],
    "pending": ["待解决的问题1", "问题2"],
    "resolved": ["已解决的问题1"]
  }},
  "todos": {{
    "pending": [
      {{"name": "待办事项名称", "priority": "high|medium|low"}}
    ],
    "completed": [
      {{"name": "已完成事项名称"}}
    ]
  }},
  "features": [
    "已实现的功能1",
    "已实现的功能2"
  ],
  "architecture": {{
    "modules": ["模块1", "模块2"],
    "tech_stack": ["技术1", "技术2"]
  }},
  "routes": [
    {{"path": "/api/xxx", "method": "GET", "description": "描述"}}
  ],
  "summary": "用一句话总结这个项目当前的状态"
}}

如果某些类别没有内容，请返回空数组或空对象。只返回 JSON，不要有其他文字。"""
    
    return prompt


def analyze_project(project_name: str, md_contents: Dict[str, str], llm_config: dict) -> Optional[Dict[str, Any]]:
    """
    使用 LLM 分析项目内容
    
    Args:
        project_name: 项目名称
        md_contents: MD 文件内容字典
        llm_config: LLM 配置
        
    Returns:
        分析结果字典，失败返回 None
    """
    if not llm_config.get("enabled", False):
        return None
    
    prompt = build_analysis_prompt(project_name, md_contents)
    result = call_llm_api(prompt, llm_config)
    
    if result:
        print(f"[LLM] Successfully analyzed project: {project_name}")
        return result
    else:
        print(f"[LLM] Failed to analyze project: {project_name}")
        return None


def test_connection(config: dict) -> dict:
    """
    测试 LLM 连接，返回详细诊断信息。

    Args:
        config: LLM 配置字典

    Returns:
        {"success": bool, "message": str, "detail": str}
    """
    import requests as req

    provider = config.get("provider", "anthropic")
    api_key = config.get("api_key", "")
    model = config.get("model", "")
    base_url = config.get("base_url", "")

    if not api_key:
        return {"success": False, "message": "API Key 未填写", "detail": ""}

    test_prompt = '请用 JSON 格式返回：{"status": "ok", "message": "connection successful"}'

    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        payload = {"model": model, "max_tokens": 100, "messages": [{"role": "user", "content": test_prompt}]}
    elif provider == "qwen":
        url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "input": {"messages": [{"role": "user", "content": test_prompt}]}, "parameters": {"result_format": "text"}}
    else:
        if not base_url:
            return {"success": False, "message": f"提供商 \"{provider}\" 需要填写 Base URL", "detail": "例如: https://api.sensenova.cn/v1 或其他 OpenAI 兼容地址"}
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": [{"role": "user", "content": test_prompt}], "temperature": 0.3}

    try:
        resp = req.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            return {
                "success": False,
                "message": f"HTTP {resp.status_code}",
                "detail": resp.text[:500],
            }
        data = resp.json()
        if provider == "anthropic":
            content = data.get("content", [{}])[0].get("text", "")
        elif provider == "qwen":
            content = data.get("output", {}).get("text", "")
        else:
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"success": True, "message": f"连接成功 ({provider} / {model})", "detail": content[:200]}
    except req.exceptions.ConnectionError as e:
        return {"success": False, "message": "网络连接失败", "detail": str(e)[:300]}
    except req.exceptions.Timeout:
        return {"success": False, "message": "请求超时 (30s)", "detail": ""}
    except Exception as e:
        return {"success": False, "message": f"异常: {type(e).__name__}", "detail": str(e)[:300]}
