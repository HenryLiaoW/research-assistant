# 学习记录

每个阶段跑通后，用自己的话写三句：加了什么、为什么这样设计、哪里是学习用的简化。
写不出来就说明还没真正看懂，回去再读一遍那一轮的"核心原理"。

## 阶段 0 —— 一次普通模型请求（2026-10-16 跑通）

验收记录：`python ask.py "用一句话解释什么是总体标准差"` → `finish_reason=stop`，prompt 26 / completion 37 tokens，耗时 1.13 秒。

1. 这轮加了什么：一个最小程序——读 .env 里的密钥，用 openai SDK 把 system prompt + 我的一句话发到 DeepSeek（OpenAI 兼容接口，只换 base_url），打印回答、finish_reason 和 token 用量。
2. 为什么密钥放 .env、参数放 config.py、逻辑放 ask.py：密钥是秘密，不能进 git；模型名、超时、输出上限这些会反复调，集中一处改一行就行；逻辑文件只管流程。三者改动频率和保密级别都不同，所以分开。
3. 学习用的简化（以后要改的地方）：没有对话历史（每次运行都是全新请求）；错误只翻译成人话、不重试；没有任何日志落盘。

练习结果：开启思考模式后同一问题 completion 从 42 → 116，prompt 从 26 → 51（服务端自己加的内容也计费）；算数题 719 token 对 14 token，两者都算对，但不思考的那个无法区分是算的还是猜的。
结论：usage 里的数字是服务端实际处理的量，不等于我发的；模型答对 ≠ 可信，数值要交给工具。

## 阶段 1 —— 单 Agent + calculate_stats 工具（2026-10-16 跑通）

验收记录：14 个离线测试 OK；"你好" 0 次工具；ARI 四个数 → 2 轮、1 次工具，mean 0.5425 / pstd 0.0444；换顺序结果一致；没给数字时模型反问而不是编造。

1. 这轮加了什么：请求里多带一份工具菜单（tools.TOOLS，JSON Schema）；模型可以不回文字、改回 tool_calls；程序查表→解析参数→校验→执行 calculate_stats→把结果以 role=tool 放回 messages→再调一次模型。整个过程是一个最多 5 轮的 while 循环（agent.run_agent）。
2. 为什么这样设计：模型不算数，只做三件语言活——从我的话里提取数字、判断该不该调工具、拿到结果后用中文转述。计算放在确定性函数里，可测试、可复现、0.3 ms。工具结果永远带 ok 字段，失败时模型拿到 ok=false 能如实说"算不了"，而不是编一个数。带 tool_calls 的 assistant 消息必须原样放回历史，否则 API 报 400，模型也会丢失"我上一轮要了什么"的上下文。
3. 学习用的简化（以后要改）：
   - 到达轮数上限时只说"停止"，工具已算出的结果被藏在日志里，应该原样附给用户（不另调模型）。
   - 工具结果里的 std_definition 每轮重复发送，白花 token，应精简或移到菜单描述里。
   - 思考模式被迫关闭：开启后带工具的多轮必须回传 reasoning_content，循环还没处理。
   - 工具报错后是否重试、怎么改参数全靠模型自觉，没有程序层面的保护。
   - 模型自己的解释（"波动不算大"）和工具结果（0.0444）在回答里混在一起，还没有标注机制。

练习结果：MAX_TOOL_ROUNDS=1 时，第 1 轮模型要工具、工具算完、循环到头，状态 max_rounds，用户看不到 0.5425。预测与实际一致。

token 账：第 1 轮 prompt 452（规则 + 问题 + 菜单），第 2 轮 631（= 452 重发 + 56 工具请求 + ≈120 工具结果）。两轮分别计费，总输入 1083。菜单每轮都在，结果从第 2 轮起才有。

## 阶段 2 上半 —— 多轮会话历史（2026-10-19 跑通）

验收记录：19 个离线测试 OK；chat.py 第 1 问调工具得 0.5425 / 0.0444；第 2 问"把最大的去掉再算"不重复数字，模型从历史取出 0.52、0.55、0.49 调工具得 0.52 / 0.0245；/history 显示 9 条消息 2 个回合；/new 后再问，模型如实说没有记录。

1. 这轮加了什么：主程序变成 chat.py。它持有一个 history 列表，每问 append 一条 user 交给 run_agent；run_agent 在同一个列表上追加工具请求、工具结果和最终回答，所以 chat.py 不需要接收返回值就拿到了完整历史。每次调用开头按 MAX_HISTORY_TURNS 整回合裁剪（从倒数第 N 条 user 切，system 永远保留）。所有结束方式（正常回答、截断、超时、轮数用尽）都先 append 一条 assistant 再 return，保证每条 user 后面必有收尾。chat.py 用 before 记住提问前的长度，API 报错时 del history[before-1:] 把这一问连同半截产物撤掉。轮数用尽时把最后一轮工具的原始结果附进回答，不再只留在日志里。
2. 为什么这样设计：API 无状态，模型"记得"只因为每次把历史整段重发——第 2 问 prompt 689 = 628（第 1 问第 2 轮的 prompt，即 system + 菜单 + 问题 + 工具请求 + 工具结果）+ 46（第 1 问的最终回答）+ 15（第 2 问本身）。历史会无限长，所以必须有上限；裁剪必须整回合删，否则 tool_calls 和 tool 结果被切散会被 API 以 400 拒绝。history / messages 是同一个列表对象，如果写成 copy()，程序不报错、测试全过，但模型从第 2 问起失忆。
3. 学习用的简化（以后要改）：
   - 裁剪是硬丢弃，早期回合原文全丢；真正重要的事实要靠阶段 2 下半的 SQLite 长期记忆另存，不能靠会话历史。
   - 历史只在内存，程序退出就没了；没有摘要压缩。
   - 回滚只覆盖 OpenAIError，其他异常（比如工具里的 bug 冒出来）不会回滚。
   - MAX_HISTORY_TURNS 的含义是"发给模型的 user 总数（含当前这问）"，设 1 等于没有历史，应至少为 2。

练习结果：MAX_HISTORY_TURNS=1 时 user_indexes[-1] 就是刚追加的那问，返回 [system, 当前问]，模型看不到任何上一轮内容。推导与裁剪时机（run_agent 开头、append 之后）一致。

自测问答（2026-10-19）：
- Q1 chat.py 没写 history = run_agent(...)，history 为什么会多出 3 条？改成 messages = history.copy() 会怎样？
  A：agent.py 第 126 行 messages = history 让两个名字指向同一个列表，之后 run_agent 里 messages.append 的工具请求、工具结果、最终回答三条就直接进了 history。每一问的 messages 都由 history 赋值而来，改成 copy() 后 append 进 messages 的内容无法同步回 history，函数退出即丢；第 2 问时模型只看到 [system, user1, user2]，没有结果也没有回答，会反问。
- Q2 MAX_HISTORY_TURNS=3，前 4 问都正常结束，第 5 问刚 append、run_agent 开始裁剪：保留哪些？模型看得到第 2 问的结果吗？
  A：保留倒数 3 个回合（第 3、4、5 问），第 5 问本身算一个；第 1、2 问整回合丢弃，模型看不到第 2 问的结果。
- Q3 第 2 问第 1 轮 prompt 689 比第 1 问第 1 轮 475 多出的 214 是什么？
  A：475 = system prompt + 工具菜单 + 第 1 问。多出的 214 = 第 1 问的工具请求（56）+ 工具结果（97）+ 第 1 问的最终回答（46）+ 第 2 问本身（15）。
- Q4 超时那处漏了 append 直接 return，会报错吗？历史尾部是什么？回滚会清掉吗？
  A：不报错；回滚只在 except OpenAIError 里，超时是正常 return，不触发。尾部停在上一轮的 [assistant(tool_calls), tool]，没有收尾；下一问 append user 后变成 [tool, user]，协议合法但模型看到未回答的工具结果，可能把上一问补答一遍。（第一次答成"尾部是超时说明的 assistant"，漏了"append 不存在"这个前提。）
- Q5 before 在哪一步记的？-1 删的是哪条？挪到 append(user) 之前该怎么写？
  A：before 在 append(user) 之后、run_agent 之前记的，是长度；before-1 就是刚追加那条 user 的下标，del history[before-1:] 把这一问连同半截产物全删。若挪到 append 之前，before 本身就是 user 的下标，写 del history[before:]。

## 阶段 2 下半 —— SQLite 长期记忆（2026-10-19 跑通）

验收记录：29 个离线测试 OK；"记住：…10 个随机种子" → save_memory id=2；/quit 重开后问"用了几个种子" → search_memory 命中，答 10 个并说明来自记忆；问截止日期 → 查空，如实说没有记录；"我今天跑了 5 公里" → 不存、不说记下了、反问是否需要记；"忘掉种子那条" → search → forget 两轮；memory.sqlite3 存在且不在 git status 里。

1. 这轮加了什么：memory.py 存储层（一张表 id/content/created_at/deleted；connect/save/search/forget/list_all，全部参数化 SQL，软删除）；tools.py 三个薄封装工具 save_memory / search_memory / forget_memory，补上模型不该管的连接和 limit；config 加库路径、检索上限和系统提示规则 6/7/8；chat.py 加 /memories 直接读库；eval/cases.md 开始累积评估用例。agent.py 不动。
2. 为什么这样设计：写入权在用户不在模型——靠提示词"只有用户明确要求记住才存"，程序不做关键词判断；检索用 LIKE 子串匹配，几十条原话级记忆够用，语义检索留给阶段 4；软删除保留追溯；参数化 SQL 让模型给的任何文字都只是值，且模型没有执行 SQL 的入口；/memories 不经模型，是核对"它到底存没存"的真相来源。
3. 提示词迭代记录：第 1 版规则 7 写"问到以前说过的事时先查"，模型把"用了几个种子"当成统计题反问、问截止日期时反问"需要我查吗"、没说记住却答"记下了"（2.5/2.6/2.7 三个用例失败）。第 2 版：触发条件改为"询问关于他自己的任何事实"，加"必须、不要反问"；规则 6 加"没调工具不能说记下了"；工具描述同步。三个用例转为通过。同一模型、同一次运行，措辞差一个"必须"行为就不同——这就是 Prompt 设计的实际内容。
4. 学习用的简化（以后要改）：
   - 规则只靠提示词约束，模型偶尔仍可能违反；程序层没有兜底（例如对 save_memory 的调用做二次确认）。
   - LIKE 匹配对同义词、换说法无效（问"随机种子数量"可能查不到"用了 10 个种子"）。
   - 每轮重发三个工具菜单 + 八条规则，首轮 prompt 从 475 涨到 909；工具越多底价越高。
   - 模型会为一个问题并行发多个 search_memory（不同关键词），合理但多占历史 token。
   - 两个 SQLite 连接（tools.py 给模型、chat.py 给 /memories）各自独立，单进程没问题，多进程/网页阶段要重新考虑。

自测问答：
- Q /memories 为什么不做成模型工具？A：它是核对视图，必须不经模型才可信；而且只读、不花 token。给用户看的核对直接读库，给模型用的能力做工具。
- Q setUp/tearDown 为什么每个测试建临时库？A：共用一个库时测试结果取决于运行顺序，时好时坏。
- Q forget 改成真 DELETE，软删测试哪个断言失败？A：只有直接查表那个（fetchone 返回 None）；search/list_all 仍为空。测试要测设计决定，不只测表面行为。
- Q 注入测试靠什么通过？A：INSERT 用 ? 占位符，content 作为值传入；改成 f-string 拼接，表会被 DROP，search 报 no such table。

## 阶段 3 —— 受限 CSV 统计工具（2026-10-20 跑通）

已完成：① 样例数据 ② csv_tools.py ⑤ 离线测试 ③ config ④ tools.py 接入 ⑥ 真机验证。

真机验收（⑥，eval/cases.md 3.1–3.9 全部通过）：53 个离线测试 OK；"我的实验里 survival_sup 的 ARI"不再误走 search_memory，按 list → describe → group_statistics 答 0.551；c_index 回答明确写出 survival_sup 基于 9 个有效值、1 个缺失；文字列求均值、读 ../.env 均被拒且无泄露；calculate_stats 回归正常。首轮 prompt 1355 tokens（阶段 2 末为 909，多出的是三个新工具菜单和规则 9）。

真机观察：
- 3.6 / 3.7 / 3.9 模型都在调用工具之前自己拒绝，代码防线未被触发。模型的拒绝靠判断，不能当安全保证；兜底的是代码，已由 run_tool 链路上的测试覆盖。
- 3.1 里模型填 group_by="config" 是推断出来的：describe 只给文字列有几种取值，没给是哪几种。猜对了；猜错也只会多一轮，数字不会错。可改进：对取值很少的文字列顺带返回取值。
- 学习用的简化（以后要改）：只支持单列分组；describe 不返回文字列取值；DATA_DIR 是相对路径，要求在项目目录里运行；data/ 里的替身会被 list_files 列出（读取时仍被拦）。

验收记录（① ② ⑤）：
- data/sample_results.csv：合成数据，3 种配置 × 10 个种子 = 30 行，列 config/seed/ari/c_index；survival_sup 第 8 个种子的 c_index 故意留空。pandas 3.0.6 读出 (30, 4)，config 为 str、其余为数值；ari 均值 0.423 / 0.551 / 0.583；c_index 计数 10 / 9 / 10。
- requirements.txt 加 pandas>=3.0,<4（下限：代码在 3.0 上验证；上限：主版本变化允许不兼容改动）。
- csv_tools.py：19 个新测试 OK，全部 48 个 OK。group_stats 返回 survival_sup 的 c_index 为 count 9 / missing 1；config 作为值列被拒并列出可用数值列；"../.env" 被拒且内容不出现。

1. 这轮加了什么：csv_tools.py，三个对外函数 list_files / describe_file / group_stats，内部 _resolve（路径白名单）和 _load（读表 + 行数上限 + 解析失败）。只返回汇总，不返回原始行。不 import config，目录和上限都作为参数传入，测试可以喂临时目录和很小的上限。
2. 为什么这样设计：
   - 和 calculate_stats 的区别是数据来源：以前数字由模型抄进参数，现在模型只给文件名和列名（数据的地址），程序自己读文件，原始数据不经过模型。
   - 判断由代码做、转述由模型做："config 不是数值列"是 is_numeric_dtype 判断出来的，模型只是读到 ok=false 后转述。
   - pandas 的 mean/count/groupby 会悄悄跳过空值，所以每组必须返回 count（实际参与计算）和 missing（被跳过），模型才不会把 9 个种子说成 10 个，也不用自己做减法。分组列为空的行用 dropna=False 单独成组。
   - 结果要被 agent.py json.dumps 发给模型，numpy 的 int64 不能序列化，所以组名 str()、数值 int()/float()。
3. 路径白名单（_resolve）五道关：
   - 上半段只看文字，不碰磁盘：第 0 道非空字符串；第 1 道不许有 / \ 和 ..；第 2 道必须 .csv 结尾。
   - 下半段看磁盘：第 3 道 resolve() 后的真实上级目录必须等于 data；第 4 道文件必须存在，不存在时列出可用文件。
   - resolve() 是 pathlib.Path 的方法：相对路径变绝对、去掉 . 和 ..、顺着符号链接找到真身；文件不存在也照样返回绝对路径，不负责检查存在。
   - 第 3 道对普通文件必然通过（base / filename 的上级就是 base），只有 resolve 后"跳走"的替身才会触发。实测：data/link.csv 指向 data 外的 secret.csv，名字通过前三道，被第 3 道拦下。
   - base 和 path 两边都要 resolve：比较的是文字，一边是 "data" 一边是完整地址就永远不相等；Mac 上 /var 实为 /private/var，也说明了这一点。
   - 错误信息只写模型给的文件名，不写替身的真实指向，避免泄露 data 以外的路径。

4. _load（我的总结）：_load 调用 _resolve 并使用它的结果——先判断白名单是否通过，再判断能否成功读取（空文件、格式错乱、不是文本这三种解析异常被接住），再判断是否超过行数上限，全部通过才返回能用的 DataFrame。任何一步失败都返回 (None, 错误)，_resolve 的错误原样往上传。保证 read_csv 拿到的永远是过了五道关的路径。

   - 行数上限：nrows=max_rows+1 搭配 len(df) > max_rows，多读的那一行用来揭露是否超限。nrows 只是读够 N 行就停、不报错；若写成 nrows=max_rows，超大文件会被截成前 N 行悄悄放行，统计结果错却没有提示。
5. list_files / describe_file：list_files 找出 data 里所有 csv，列出名字和大小；不读内容、不接收模型给的文件名，所以不需要走 _resolve（data 里的替身会被列出，但读取时仍会被拦）。describe_file 走 _load，逐列给出概况：name、dtype、numeric、missing、unique（nunique，几种不同取值，不含空值）。unique 只能帮模型排除明显不适合分组的列，选哪列主要靠列名和用户的问题；选错分组列的后果是答非所问，数仍然算得对。
6. group_stats（我的总结）：
   - value_column 是"算谁"（必须是数值列），group_by 是"按什么分组"（可以是文字列，可以不填）。value_column 本身是列名字符串，df[value_column] 才是那一列的数据——第一道查名字存在，第二道查内容是数值。先查存在再取列，顺序不能反。
   - group_by 为 None、"" 或全空格都当作不分组：先 isinstance 再 .strip()，靠 and/or 短路防止对 None 或数字调用 strip。分组时再查三道：列存在、不能和值列相同、组数不超 max_groups。
   - 不分组得到 [("全部", 整列)]，分组得到 [(组名, part[value_column]), ...]，两条路形状一样，后面统一计算。part 是某组的小表，part[value_column] 只取要算的那一列。
   - dropna=False：分组列为空的行单独成"(缺失)"组。默认会悄悄丢掉这些行，总数对不上、数据问题被藏起来，可能丢掉的恰好是最重要的一行。
   - 逐组：从 groups 取 label 和 values，values.dropna() 得到 valid；count 用 valid、missing 用 values；全空组四个统计量填 None 不填 0；std 用 ddof=0（总体标准差，和 calculate_stats 一致，pandas 默认是 ddof=1）。最后连同文件、列、分组、std 定义一起返回。
   - 组名不 str() 的后果：按 seed 分组时组名是 numpy.int64，csv_tools 本身不报错，但 agent.py 里 json.dumps 抛 TypeError，发生在 run_tool 的保护之外，chat.py 只接 OpenAIError，整个程序崩溃；且只在按数值列分组时出现。
7. config.py（③）：加 DATA_DIR / MAX_CSV_ROWS / MAX_GROUPS；改规则 1（只管用户在对话里直接给出的数字）、规则 7（实验表格数据不算个人事实，按规则 9 处理）；加规则 9（先 list / describe 再 group_statistics，文件名和列名必须来自工具结果，回答说明有效值和缺失数）。教训：每加一个工具，都要回头检查旧规则会不会把模型带偏——规则 1、7 当初故意写得很强硬，现在会误伤新工具。
8. tools.py 接入（④）：目的是把模型和 csv_tools.py 接通。菜单让模型知道有这个工具，登记表让 run_tool 找得到函数，包装函数把调用转给 csv_tools 并补上 config，import 让 tools.py 能用 csv_tools。
   - 为什么要包装函数，不直接登记 csv_tools.list_files：不是为了统一格式，而是 csv_tools 的函数需要 data_dir、max_rows、max_groups，这些由 config 决定，不能让模型给。直接登记的话，模型不传就报错，让模型传就失去安全。包装函数的参数列表，就是模型能控制的全部范围（phase 2 的 search_memory(keyword) 同理）。
   - 和路径白名单的关系：_resolve 里"文件真实位置必须就在允许的目录 base 里"这项检查，base 是由 data_dir 算出来的。如果模型能传 data_dir="/Users/henry/Downloads"、file="bank_statement.csv"，_resolve 的每一项检查都会通过——不是检查写错了，而是"允许的目录"本身被换掉了。安全靠两层：_resolve 检查文件在不在圈里，包装函数保证圈只能由 config 画。
   - 两层都挡：菜单里不写 data_dir，模型不知道；就算硬塞进来，包装函数参数列表不接收，impl(**args) 报 TypeError，被 run_tool 接住返回 ok=false。原函数没被禁掉，包装函数内部照样调用它，只是模型唯一能进的门是包装函数。
9. 规则 10（实验条件混比）：统计或比较时不同实验条件不能混成一个数代表某种方法；用户没说明时默认按条件列分组，明确要总体时可以算但必须说明混合了哪几种条件。放在提示词而不是代码：工具不分组算整列并没有算错，错的是"该不该分组"的判断，这是语言层面的决定。真机 3.10 / 3.11 通过；3.11 只说"混合 3 种条件"没列名字，和 describe 不返回文字列取值是同一个限制。
练习结果：
- Q 只返回 mean=0.711，模型说"10 个种子平均 0.711"错在哪？A（我的）：没发现有缺失值，工具应多返回缺失数量。补充：错在样本量描述，应同时返回 count 和 missing，让模型不用自己做减法。
- Q 模型要求算 config 列的均值，工具怎么回应？A：第一次答成"按 config 分组算"，混淆了 group_by 和 value_column。正确：config 是文字列，返回 ok=false，说明原因并列出可用数值列。
- Q 不改规则 7，问"我的实验里 survival_sup 的 ARI 是多少"会怎样？A：命中"我的……"句式，模型调 search_memory，查不到就按规则 7 说"没有记录"，而数据明明在 CSV 里；若记忆里恰好有含 ARI 的旧记录，还会被拿来答非所问。
- Q 规则 9 为什么强调文件名和列名必须来自工具结果？A：数字准不准由代码保证，猜错列名（ari 写成 ARI，区分大小写）只会被拒。规则 9 防的是模型瞎试：多一轮调用、浪费 token、可能撞上 MAX_TOOL_ROUNDS、被拒几次后放弃并说"没有这个数据"。代码负责"错了也不会出错的数"，提示词负责"尽量一次做对"。
- Q link.csv 过了哪几道、被哪一行拦？A：第 0、1、2 道都过，被第 3 道 if path.parent != base 拦下。

## 阶段 4a —— PDF 解析与解析检查（2026-10-21）

验收记录：`python build_index.py --check` 对论文（85 页）和合成样例协议（3 页）输出解析报告；21 个文档解析测试 OK。论文：正文字号 11.0，84 页识别出印刷页码，10 页几乎无文字（证书截图、整页图），带编号的章节标题 95 个，与论文目录逐条对上。

1. 这轮加了什么：documents.py 的 parse_pdf 把 PDF 解析成"页 → 行"，每行带字号、是否整行加粗、是否标题，每页记下 PDF 页码和印刷页码；build_index.py --check 打印解析报告（页数、字数、正文字号、页码示例、几乎无文字的页、全部带编号的标题、前中后三段抽样）；scripts/make_sample_protocol.py 生成合成样例协议（与 sample_results.csv 对得上，附录含一句提示注入测试句），可提交 git；论文放 docs/，被 .gitignore 排除。
2. 为什么先检查再建索引：解析坏了不会报错，只会让检索悄悄变差。这一步实际发现并解决了：
   - pypdf 普通模式把章节标题并进正文，排版模式拆碎单词 → 换 PyMuPDF（AGPL，公开项目不受影响）；
   - 论文没有书签 → 按字号识别标题（正文 11，节 13，章 16.1）；
   - 页码在页尾但抽取时排第一行 → 按页面位置（上下各 8% 页边）识别，而不是按"第几行"；
   - 附录 H.1–L.5 是加粗 11 号 → 加"整行加粗 + 以编号开头"规则；只看加粗会误判表头 Element/Setting；
   - 三级标题 2.2.1 等是 12 号不加粗（全文正好 18 行）→ 加"比正文稍大 + 以编号开头"规则；
   - 自己的 bug：拼接文字时丢掉了只有空白的片段，编号和标题之间的空格（另一种字体画的）丢失，31 行粘连（"2.2.1Clinical""Chapter 1:Introduction"）→ 拼文字用全部片段，算字号和加粗只用有字的片段。
   改前改后：带编号标题 59 → 77 → 95；三级标题 0/18 → 18/18；附录小节 0/14 → 14/14；粘连行 31 → 0；页码识别 84 页不变。
3. 验证方法的教训：第一版页码改动的验证（"还是 22、还是 84 页"）在没改时也会通过，区分不了改没改。好的验证必须在没改时失败；之后改为先放会失败的测试，再改代码让它通过。报告只列前 15 个标题时发现不了漏识别，改为列出全部标题并与论文目录对照。
4. 学习用的简化（以后要改）：标题规则是针对 Word 导出文档的启发式（字号、加粗、编号），不带编号且只靠加粗的标题仍认不出；图片里的内容读不到（不做 OCR）；表格被拆成逐行文字，列对应关系丢失；扫描件不支持。
