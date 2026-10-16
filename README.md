# research-assistant — AI 论文与实验记录助手

逐阶段搭建的学习项目。**当前进度：阶段 0 —— 完成一次普通模型请求。**

## 当前能做什么

- `python ask.py "一句话"`：把这句话发给 DeepSeek API，打印回答、耗时和 token 用量。
- 没有工具调用、没有记忆、没有 RAG、没有网页（后续阶段逐步加入）。

## 安装与运行（macOS）

```bash
cd /Users/henry/AAI/research-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 然后编辑 .env，填入你的 DEEPSEEK_API_KEY
python ask.py "用一句话解释什么是总体标准差"
```

## 文件说明

| 文件 | 职责 |
|---|---|
| `config.py` | 模型名、思考模式、超时、重试、输出上限、系统提示词 —— 所有"容易想改"的参数 |
| `ask.py` | 读密钥 → 建客户端 → 发一次请求 → 打印结果；把常见 API 错误翻译成人话 |
| `.env.example` | 密钥占位符；复制为 `.env` 后填真实密钥（`.env` 不进 git） |
| `requirements.txt` | 依赖：`openai`（DeepSeek 用 OpenAI 兼容接口）、`python-dotenv` |

## 会发送给模型 API 的内容

`config.SYSTEM_PROMPT` + 你在命令行输入的那一句话。不会发送本机任何文件。
