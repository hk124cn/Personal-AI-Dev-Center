"""
LLM 分析功能测试脚本

使用方法：
1. 在 config.json 中配置 LLM API key
2. 运行: python backend/test_llm.py
3. 查看测试结果
"""

import json
from pathlib import Path
from llm_analyzer import build_analysis_prompt, call_llm_api

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_llm_connection():
    """测试 LLM API 连接"""
    print("=" * 60)
    print("测试 1: LLM API 连接")
    print("=" * 60)
    
    config = load_config()
    llm_config = config.get("llm", {})
    
    if not llm_config.get("enabled", False):
        print("❌ LLM 未启用，请在 config.json 中设置 enabled: true")
        return False
    
    api_key = llm_config.get("api_key", "")
    if not api_key or "YOUR_API_KEY" in api_key:
        print("❌ API Key 未配置，请在 config.json 中填入正确的 API key")
        return False
    
    provider = llm_config.get("provider", "anthropic")
    model = llm_config.get("model", "claude-3-5-sonnet-20241022")
    
    print(f"✓ LLM 已启用")
    print(f"  Provider: {provider}")
    print(f"  Model: {model}")
    
    # 发送一个简单的测试请求
    test_prompt = "请用 JSON 格式返回：{\"status\": \"ok\", \"message\": \"connection successful\"}"
    
    try:
        result = call_llm_api(test_prompt, llm_config)
        if result and result.get("status") == "ok":
            print(f"✓ API 连接成功")
            print(f"  Response: {result}")
            return True
        else:
            print(f"❌ API 响应异常: {result}")
            return False
    except Exception as e:
        print(f"❌ API 调用失败: {str(e)}")
        return False


def test_project_analysis():
    """测试项目分析功能"""
    print("\n" + "=" * 60)
    print("测试 2: 项目 MD 文档分析")
    print("=" * 60)
    
    config = load_config()
    llm_config = config.get("llm", {})
    
    if not llm_config.get("enabled", False):
        print("⚠️  跳过此测试（LLM 未启用）")
        return
    
    # 使用最新的同步数据
    data_path = BASE_DIR / "backend" / "data" / "latest.json"
    if not data_path.exists():
        print("❌ 未找到同步数据，请先运行 sync.py")
        return
    
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # 选择一个有 MD 文件的项目进行测试
    test_project = None
    for project in data["projects"]:
        if project.get("md_files") and len(project["md_files"]) > 0:
            test_project = project
            break
    
    if not test_project:
        print("❌ 没有找到包含 MD 文件的项目")
        return
    
    print(f"选择项目: {test_project['name']}")
    print(f"MD 文件数量: {len(test_project['md_files'])}")
    print(f"文件列表: {', '.join(test_project['md_files'][:5])}")
    
    # 模拟从 SSH 读取的原始内容（这里用示例内容代替）
    sample_md_content = {
        "README.md": """# DEMO-PROJECT 自动化股票评分系统

## 项目概述
自动化股票评分系统，每日盘后拉取数据，进行评分。

## 技术架构
- 核心引擎: src/core/scoring_engine.py
- 因子管理: src/core/factor_manager.py
- 数据层: src/datafactory/data_manager.py

## API 路由
- GET /api/stocks - 获取股票列表
- POST /api/score - 计算评分

## 待办事项
- [ ] 优化评分算法
- [x] 实现财报评分
- [ ] 添加自动交易功能

## 已知问题
- 数据更新延迟较大
- 部分因子计算耗时过长
""",
        "TODO.md": """# TODO List

## 进行中
- [ ] 回测系统开发
- [ ] 性能优化

## 已完成
- [x] 基础评分引擎
- [x] 多因子支持
""",
    }
    
    # 构建提示词并调用 LLM
    prompt = build_analysis_prompt(test_project["name"], sample_md_content)
    print(f"\n发送分析请求...")
    
    try:
        result = call_llm_api(prompt, llm_config)
        
        if result:
            print("✓ 分析成功！")
            print("\n分析结果:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return True
        else:
            print(" 分析失败，未返回结果")
            return False
    except Exception as e:
        print(f"❌ 分析过程出错: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("\n" + "=" * 60)
    print("Dev Center - LLM 分析功能测试")
    print("=" * 60)
    
    # 测试 1: API 连接
    connection_ok = test_llm_connection()
    
    if not connection_ok:
        print("\n⚠️  API 连接测试失败，请检查配置后重试")
        print("\n配置步骤:")
        print("1. 编辑 config.json")
        print("2. 在 'llm' 部分填入正确的 API key")
        print("3. 设置 'enabled': true")
        print("\n参考文件: config.llm.example.json")
        return
    
    # 测试 2: 项目分析
    analysis_ok = test_project_analysis()
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
    
    if analysis_ok:
        print("✓ 所有测试通过！")
        print("\n下一步:")
        print("运行 'python backend/sync.py' 开始完整同步")
    else:
        print("⚠️  部分测试未通过，请检查配置或网络")


if __name__ == "__main__":
    main()
