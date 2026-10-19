# research-assistant — AI 论文与实验记录助手

逐阶段搭建的学习项目。**当前进度：阶段 2（上半）—— 单 Agent + 工具 + 多轮会话历史。**

## 当前能做什么

- `python ask.py "一句话"`：阶段 0，一次普通模型请求，打印回答、耗时和 token 用量。
- `python agent.py "问题"`：阶段 1，单条请求的 Agent 循环。涉及统计时模型调用 `calculate_stats` 工具，数值由 Python 计算，模型只负责提取数字和转述结果。
- `python chat.py`：阶段 2 上半，多轮对话。同一次运行内模型能看到之前的回合；`/new` 重开，`/history` 查看，`/quit` 退出。历史只保留最近 `MAX_HISTORY_TURNS` 个回合，整回合裁剪。
- 还没有：跨运行的长期记忆（阶段 2 下半，SQLite）、实验 CSV 工具、RAG、网页。

## 安装与运行（macOS）

```bash
cd /Users/henry/AAI/research-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python ask.py "用一句话解释什么是总体标准差"
python agent.py "帮我计算这组ARI的均值和总体标准差：0.52、0.55、0.61、0.49。"
python chat.py
python -m unittest discover -s tests -v
```

复制 `.env.example` 后编辑 `.env`，填入你的 `DEEPSEEK_API_KEY`。

## 文件说明

| 文件 | 职责 |
|---|---|
| `config.py` | 模型名、思考模式、超时、重试、输出上限、循环轮数与时间预算、历史回合上限、系统提示词 |
| `llm_client.py` | 读密钥、建客户端、把常见 API 错误翻译成人话 |
| `ask.py` | 阶段 0：一次请求 |
| `tools.py` | 工具菜单（JSON Schema）+ 工具实现 + `run_tool` 统一入口（永不抛异常，失败返回 `ok=false`） |
| `agent.py` | Agent 循环 `run_agent(client, history)`：调模型 → 执行工具 → 回填结果 → 直到最终回答；`trim_history` 整回合裁剪 |
| `chat.py` | 多轮对话的命令行界面，维护 `history` 列表并逐轮交给 `run_agent` |
| `tests/` | 离线单元测试：工具边界条件、历史裁剪 |
| `NOTES.md` | 每阶段的学习记录与设计取舍 |
| `RESUME_TARGET.md` | 阶段 4 的验收标准（简历目标稿） |
| `.env.example` | 密钥占位符；复制为 `.env` 后填真实密钥（`.env` 不进 git） |

## 会发送给模型 API 的内容

系统提示词、你在本次会话中输入的问题（最多最近 `MAX_HISTORY_TURNS` 个回合）、工具菜单、
模型发出的工具请求与程序回填的工具结果。不会发送本机任何文件。
