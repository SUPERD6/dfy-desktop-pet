# 大肥鱼大脑单元测试

`test_brain.py` 用标准库 `unittest` 覆盖「大脑与记忆」这一层；全程只用本地
`http.server` 在 `127.0.0.1` 随机端口上起一个假的 OpenAI 兼容接口（`/chat/completions`），
**不访问外网、不需要真实 API Key、不读写仓库里的 `pet/memory.json`**（每个用例用
`tempfile.TemporaryDirectory` 临时目录）。

## 运行

在仓库根目录下执行：

```powershell
python -m unittest discover -s pet\tests -v
```

## 实际输出（2026-10-25，本机实测）

```text
test_required_interface (test_brain.InterfaceTests.test_required_interface)
Lead 的 app.py 依赖的接口签名都在。 ... ok
test_401_falls_back_to_offline_error_without_leaking_key (test_brain.LLMClientTests.test_401_falls_back_to_offline_error_without_leaking_key)
401：Key 无效 → offline-error，日志里不能出现 Key。 ... ok
test_429_exhausted_falls_back (test_brain.LLMClientTests.test_429_exhausted_falls_back)
429 用尽重试 → offline-error（不抛异常给界面）。 ... ok
test_429_retry_then_success (test_brain.LLMClientTests.test_429_retry_then_success)
429 限流：允许重试一次，第二次成功。 ... ok
test_bad_response_falls_back (test_brain.LLMClientTests.test_bad_response_falls_back)
接口返回坏 JSON / 空 choices → offline-error。 ... ok
test_key_priority_config_env_file (test_brain.LLMClientTests.test_key_priority_config_env_file)
Key 优先级：config → 环境变量 DFY_API_KEY → pet/.secret_api_key。 ... ok
test_llm_options_defaults_and_string_values (test_brain.LLMClientTests.test_llm_options_defaults_and_string_values)
config 里缺字段用默认值；界面存的字符串数字也能解析；非法值回默认。 ... ok
test_success_path_llm_reply (test_brain.LLMClientTests.test_success_path_llm_reply)
正常回复：走 /chat/completions，返回 llm，并写入记忆。 ... ok
test_test_connection_ok_and_failures (test_brain.LLMClientTests.test_test_connection_ok_and_failures)
设置窗「测试连接」：成功/401/无 Key 三种人话，都不含 Key。 ... ok
test_timeout_falls_back_to_offline_error (test_brain.LLMClientTests.test_timeout_falls_back_to_offline_error)
超时：0.2 秒超时、服务端睡 1.2 秒 → offline-error，且不拖太久。 ... ok
test_app_style_direct_memory_access (test_brain.MemoryTests.test_app_style_direct_memory_access)
兼容 app.py 设置窗直接写 brain.memory["user_name"] 的旧习惯。 ... ok
test_corrupt_memory_json_self_heals (test_brain.MemoryTests.test_corrupt_memory_json_self_heals)
memory.json 半截/乱码时不崩，坏文件留档，记忆按空处理。 ... ok
test_history_limit_60 (test_brain.MemoryTests.test_history_limit_60)
对话历史只保留最近 60 条。 ... ok
test_memory_summary_initial (test_brain.MemoryTests.test_memory_summary_initial) ... ok
test_memory_write_reload_and_retrieval (test_brain.MemoryTests.test_memory_write_reload_and_retrieval)
事实/称呼写入后能重载，并能按新输入检索到。 ... ok
test_old_memory_json_self_heals (test_brain.MemoryTests.test_old_memory_json_self_heals)
旧格式 memory.json（facts 是字符串、无 version）能读、能升级、不丢数据。 ... ok
test_disabled_model_ignores_stale_key (test_brain.OfflineAndPromptTests.test_disabled_model_ignores_stale_key) ... ok
test_offline_rules (test_brain.OfflineAndPromptTests.test_offline_rules)
没启用模型：命中关键词用对应台词，没命中用兜底台词，不发网络请求。 ... ok
test_status_text_variants (test_brain.OfflineAndPromptTests.test_status_text_variants) ... ok
test_system_prompt_contains_memory (test_brain.OfflineAndPromptTests.test_system_prompt_contains_memory)
system prompt 里要有最近对话 + 相关记忆，且不泄漏 Key。 ... ok
test_line_for_dsh_state (test_brain.ProactiveAndDSHTests.test_line_for_dsh_state)
running/success/failure/approval 给候选台词，idle/未知返回 None。 ... ok
test_proactive_contexts (test_brain.ProactiveAndDSHTests.test_proactive_contexts)
久未互动 / 工作状态 / 有上文时都能生成一句人话。 ... ok
test_proactive_llm_failure_uses_offline (test_brain.ProactiveAndDSHTests.test_proactive_llm_failure_uses_offline) ... ok
test_proactive_offline_dedupes_within_20 (test_brain.ProactiveAndDSHTests.test_proactive_offline_dedupes_within_20)
离线主动搭话：按时段模板，最近 20 条内不重复轰炸。 ... ok
test_proactive_with_llm_and_repeat_fallback (test_brain.ProactiveAndDSHTests.test_proactive_with_llm_and_repeat_fallback)
有模型时用模型生成；模型又返回重复句时退回离线模板。 ... ok

----------------------------------------------------------------------
Ran 25 tests in 3.079s

OK
```

## 覆盖点

| 分类 | 用例 | 说明 |
| --- | --- | --- |
| LLM 成功 | `test_success_path_llm_reply` | 请求打到本地假接口的 `/v1/chat/completions`；校验 system prompt、`stream:false`、Bearer 头；返回 `("...", "llm")` 并落盘记忆 |
| Key 无效 | `test_401_falls_back_...` | 401 → `offline-error`；断言日志里有友好说明、**不含 Key 原文** |
| 超时 | `test_timeout_falls_back_...` | 客户端 0.2s 超时 vs 服务端 1.2s，→ `offline-error`，整体耗时 < 2.5s |
| 限流/重试 | `test_429_retry_then_success` / `..._exhausted_falls_back` | 429 可重试一次；重试用尽也不抛异常 |
| 返回格式坏 | `test_bad_response_falls_back` | 非 JSON / 空 `choices` 都回退 |
| 配置解析 | `test_llm_options_defaults_and_string_values` | timeout_s/max_tokens/temperature/max_retries 缺省值、Tk 存进来的字符串数字、非法值回默认 |
| Key 优先级 | `test_key_priority_config_env_file` | config → `DFY_API_KEY` → `pet/.secret_api_key`；文件里的空白会 strip；三处都没有时不发请求 |
| 连接测试 | `test_test_connection_ok_and_failures` | 成功 / 401 / 无 Key 文案，均不含 Key |
| 记忆写入与检索 | `test_memory_write_reload_and_retrieval` | 名字、喜好入记忆；重启后仍在；按「蓝色」能检索到；system prompt 注入 |
| 记忆容量/兼容 | `test_history_limit_60`、`test_old_memory_json_self_heals`、`test_corrupt_memory_json_self_heals`、`test_app_style_direct_memory_access` | 历史 60 条上限；旧格式自动升级 v2；坏文件不崩并留 `.corrupt`；兼容 app.py 直接写 `brain.memory["user_name"]` |
| 离线规则 | `test_offline_rules`、`test_disabled_model_ignores_stale_key` | 关键词命中对应台词、未命中用兜底、不发网络请求 |
| 主动搭话 | `test_proactive_offline_dedupes_within_20`、`test_proactive_contexts`、`test_proactive_with_llm_and_repeat_fallback`、`test_proactive_llm_failure_uses_offline` | 时段模板、最近 20 条去重、模型生成、模型失败/重复时退回离线 |
| DSH 状态 | `test_line_for_dsh_state` | running/success/failure/approval 各返回候选台词；idle/未知返回 `None`；`tool` 归到 running |
| 接口签名 | `test_required_interface` | app.py 依赖的 8 个接口都在且类型正确 |

## 备注

- 用例启动前会临时移除环境变量 `DFY_API_KEY`（结束后还原），避免本机真实的 Key 干扰优先级测试。
- 测试只监听 `127.0.0.1`，不访问真实 `api.deepseek.com`，因此断网也能跑。
- 假接口支持 `ok / 401 / 429 / 500 / sleep（超时）/ bad_json / empty_choices` 及顺序脚本（`script`），
  新增用例时可通过 `FakeOpenAI.configure(...)` 切换。
- 测试不会在 `pet/` 下产生 `memory.json`；若手动在仓库外跑出文件，可安全删除。


## Agent 层（`test_agent.py`，2026-10-25 新增）

`pet/agent.py` 是让她「真的动手」的一层：DeepSeek / OpenAI 兼容的 function calling 循环
（最多 6 步）+ 8 个本机工具（6 个只读、2 个需主人确认）。测试同样只用本地
`http.server` 假接口（按脚本返回 `tool_calls` / 错误 / 慢响应），文件操作全部在
`tempfile.TemporaryDirectory` 里；两个 PowerShell 用例只执行 `Write-Output` / `Start-Sleep`
这类无害命令。全程不联网、不调真实 API、不碰真实个人库和工作区。

运行（仓库根目录）：

```powershell
python -m unittest pet.tests.test_agent -v
```

### 实际输出（2026-10-25，本机实测）

```text
test_401_returns_friendly_error_without_key (pet.tests.test_agent.ApiErrorTests.test_401_returns_friendly_error_without_key) ... ok
test_bad_json_response (pet.tests.test_agent.ApiErrorTests.test_bad_json_response) ... ok
test_key_priority_config_env_file (pet.tests.test_agent.ApiErrorTests.test_key_priority_config_env_file) ... ok
test_missing_choices_response (pet.tests.test_agent.ApiErrorTests.test_missing_choices_response) ... ok
test_no_key_returns_error (pet.tests.test_agent.ApiErrorTests.test_no_key_returns_error) ... ok
test_timeout_returns_error (pet.tests.test_agent.ApiErrorTests.test_timeout_returns_error) ... ok
test_confirm_exception_treated_as_reject (pet.tests.test_agent.ConfirmTests.test_confirm_exception_treated_as_reject) ... ok
test_dangerous_command_rejected_without_confirm (pet.tests.test_agent.ConfirmTests.test_dangerous_command_rejected_without_confirm)
明显破坏性命令直接拒，不打扰主人确认。 ... ok
test_run_powershell_confirm_false_does_not_execute (pet.tests.test_agent.ConfirmTests.test_run_powershell_confirm_false_does_not_execute)
confirm 拒绝时命令绝不执行，模型收到拒绝结果。 ... ok
test_sensitive_command_rejected_without_confirm (pet.tests.test_agent.ConfirmTests.test_sensitive_command_rejected_without_confirm)
命令里提到敏感文件（Key/.env/id_rsa/*.pem）直接拒，不弹确认。 ... ok
test_shell_success_output (pet.tests.test_agent.ConfirmTests.test_shell_success_output)
确认后能真的执行无害命令，工作目录在项目根（这里临时目录）。 ... ok
test_shell_timeout_kills_process (pet.tests.test_agent.ConfirmTests.test_shell_timeout_kills_process)
命令超时：杀掉进程、返回 timeout，不卡死 run()。 ... ok
test_write_file_after_confirm_true (pet.tests.test_agent.ConfirmTests.test_write_file_after_confirm_true)
confirm 点头才写文件；args/preview 都给 confirm 看得到。 ... ok
test_write_file_confirm_none_rejected_end_to_end (pet.tests.test_agent.ConfirmTests.test_write_file_confirm_none_rejected_end_to_end)
没有确认弹窗时拒绝写文件，模型收到拒绝结果并继续回答。 ... ok
test_binary_files_rejected (pet.tests.test_agent.PathSafetyTests.test_binary_files_rejected) ... ok
test_default_allowed_roots_is_project_only (pet.tests.test_agent.PathSafetyTests.test_default_allowed_roots_is_project_only)
不覆盖 allowed_roots 时，默认白名单只有项目根；个人库要在配置里显式开放。 ... ok
test_list_dir_and_search_files (pet.tests.test_agent.PathSafetyTests.test_list_dir_and_search_files) ... ok
test_outside_allowed_root_rejected (pet.tests.test_agent.PathSafetyTests.test_outside_allowed_root_rejected) ... ok
test_path_traversal_rejected (pet.tests.test_agent.PathSafetyTests.test_path_traversal_rejected) ... ok
test_read_file_basic_and_truncated (pet.tests.test_agent.PathSafetyTests.test_read_file_basic_and_truncated) ... ok
test_secret_files_rejected (pet.tests.test_agent.PathSafetyTests.test_secret_files_rejected) ... ok
test_too_large_read_rejected (pet.tests.test_agent.PathSafetyTests.test_too_large_read_rejected) ... ok
test_write_file_limits (pet.tests.test_agent.PathSafetyTests.test_write_file_limits) ... ok
test_dsh_status_adapter (pet.tests.test_agent.ReadonlyToolTests.test_dsh_status_adapter) ... ok
test_personal_adapter (pet.tests.test_agent.ReadonlyToolTests.test_personal_adapter) ... ok
test_prompt_block_legacy_no_arg_signature (pet.tests.test_agent.ReadonlyToolTests.test_prompt_block_legacy_no_arg_signature) ... ok
test_run_works_when_personal_and_overview_missing (pet.tests.test_agent.ReadonlyToolTests.test_run_works_when_personal_and_overview_missing)
两个模块都没接上时，run() 仍能正常走完一轮并回话。 ... ok
test_work_overview_adapter (pet.tests.test_agent.ReadonlyToolTests.test_work_overview_adapter) ... ok
test_key_not_echoed_in_reply (pet.tests.test_agent.RedactionTests.test_key_not_echoed_in_reply)
模型万一复述了 Key，最终回复里也要抹掉。 ... ok
test_key_not_in_system_prompt (pet.tests.test_agent.RedactionTests.test_key_not_in_system_prompt) ... ok
test_tool_output_redacts_key (pet.tests.test_agent.RedactionTests.test_tool_output_redacts_key)
工具读到的内容里就算有 Key，也不能原样进模型上下文/步骤。 ... ok
test_answer_without_tool (pet.tests.test_agent.RunLoopTests.test_answer_without_tool)
模型直接回答：不调工具，source=agent，steps 为空。 ... ok
test_empty_user_text_no_request (pet.tests.test_agent.RunLoopTests.test_empty_user_text_no_request)
空输入：直接返回 error，不发请求。 ... ok
test_max_steps_stops_at_six (pet.tests.test_agent.RunLoopTests.test_max_steps_stops_at_six)
模型一直要工具：第 6 步停下，给可读失败说明。 ... ok
test_multiple_tool_calls_in_one_round (pet.tests.test_agent.RunLoopTests.test_multiple_tool_calls_in_one_round)
一轮里多个 tool_calls：都要执行，且按返回顺序进 steps。 ... ok
test_on_step_callback_error_does_not_break_run (pet.tests.test_agent.RunLoopTests.test_on_step_callback_error_does_not_break_run)
UI 的 on_step 回调抛异常也不能打断 agent。 ... ok
test_two_round_tool_call_then_reply (pet.tests.test_agent.RunLoopTests.test_two_round_tool_call_then_reply)
第一轮 tool_calls、第二轮最终回复：顺序、steps、reply 都对。 ... ok
test_mutating_tools_are_marked (pet.tests.test_agent.ToolSpecTests.test_mutating_tools_are_marked) ... ok
test_tool_specs_are_openai_compatible (pet.tests.test_agent.ToolSpecTests.test_tool_specs_are_openai_compatible) ... ok

----------------------------------------------------------------------
Ran 39 tests in 5.084s

OK
```

### Agent 层覆盖点

| 分类 | 用例 | 说明 |
| --- | --- | --- |
| 两轮工具调用 | `test_two_round_tool_call_then_reply` | 第一轮 `tool_calls(get_work_overview)`、第二轮最终回复；断言请求顺序、`steps`、`reply`、`on_step`、system prompt 里的人设/工具/个人库摘要 |
| 一轮多工具 | `test_multiple_tool_calls_in_one_round` | 同一条 assistant 消息里多个 `tool_calls` 按顺序执行、`tool_call_id` 一一对应 |
| 直接回答 | `test_answer_without_tool` | 不需要工具时 `steps=[]`，`source="agent"` |
| 步数上限 | `test_max_steps_stops_at_six` | 模型一直要工具时第 6 步停，`source="error"` + 可读失败说明 |
| 接口失败 | `test_401_...`、`test_timeout_...`、`test_bad_json_response`、`test_missing_choices_response` | 401 / 超时 / 坏 JSON / 缺 `choices` → `source="error"`，不抛异常、不泄漏 Key |
| Key 优先级 | `test_key_priority_config_env_file` | config → `DFY_API_KEY` → `pet/.secret_api_key`；`Authorization: Bearer` 正确 |
| 确认拒绝 | `test_write_file_confirm_none_rejected_end_to_end`、`test_run_powershell_confirm_false_does_not_execute` | `confirm=None` / 返回 False 时不写文件、不执行命令，模型收到 `{ok:false, rejected:true}` |
| 确认点头 | `test_write_file_after_confirm_true`、`test_shell_success_output` | confirm 收到 `(tool_name, args, preview)`；写文件 / 无害命令真的执行 |
| 破坏性命令 | `test_dangerous_command_rejected_without_confirm` | `Remove-Item -Recurse` / `format` / `shutdown` / `del /s` / `reg delete` / `bcdedit` 直接拒，不弹确认 |
| 敏感命令 | `test_sensitive_command_rejected_without_confirm` | 命令里提到 `.secret_api_key` / `.env` / `id_rsa` / `*.pem` / `*.key` / `credentials` 直接拒，不弹确认；正常命令不误伤 |
| 路径安全 | `test_path_traversal_rejected`、`test_secret_files_rejected`、`test_outside_allowed_root_rejected`、`test_default_allowed_roots_is_project_only` | `..` 穿越、`.secret_api_key` / `.env` / `*.key`、白名单外路径都拒绝；默认白名单只有项目根（个人库需显式开放） |
| 读写边界 | `test_read_file_basic_and_truncated`、`test_too_large_read_rejected`、`test_binary_files_rejected`、`test_write_file_limits`、`test_list_dir_and_search_files` | 截断标注「已截断」、>5MB 拒读、二进制拒读、写文件只限 9 种文本扩展名且 ≤200KB、敏感文件名不出现在 `list_dir` 里 |
| 工具适配 | `test_work_overview_adapter`、`test_personal_adapter`、`test_dsh_status_adapter`、`test_run_works_when_personal_and_overview_missing`、`test_prompt_block_legacy_no_arg_signature` | `personal`/`overview` 缺失或报错时返回「暂不可用」、不抛异常；兼容 `prompt_block()` / `find(query)` 旧签名；DSH 状态心跳过期给 `connected:false` |
| Key 脱敏 | `test_tool_output_redacts_key`、`test_key_not_echoed_in_reply`、`test_key_not_in_system_prompt`、`test_401_...` | 工具结果、模型回复、system prompt、steps / error / 日志里都不出现 Key |
| 工具定义 | `test_tool_specs_are_openai_compatible`、`test_mutating_tools_are_marked` | 8 个工具定义符合 OpenAI tools schema、名字不重复、两个需确认工具标记正确 |

### 备注

- 两个 PowerShell 用例在找不到 `powershell` / `pwsh` 的机器上自动 `skip`，不影响其余用例。
- 假接口支持「按顺序脚本」：`say(文本)` 普通回复、`call(工具名, 参数)` 一次工具调用、
  `calls(...)` 一轮多调用、`api_error(401)` 错误、`slow(秒)` 慢响应、`raw_body(bytes)` 坏响应。
- `run()` 返回固定为 `{"reply", "steps", "source", "error"}`；`source` 只有 `"agent"` / `"error"`，
  出错时 app 可回退 `brain.reply()` 普通聊天。



## 个人库与工作总览（2026-10-25，overview-dev）

新增两个模块的单元测试，全部用 `tempfile` 造假个人库 / 假工作区 / 假 DSH 目录，
AI 建议用本地 `http.server` 假 OpenAI 兼容接口，**不联网、不碰真实个人库、不写仓库里的
`pet/overview.json`**：

| 文件 | 覆盖 |
| --- | --- |
| `test_personal.py`（20 个） | 缺文件优雅降级、只读不写盘、字段提炼、`prompt_block` ≤ `max_chars`、技术坑（`topic=pitfall`）不进人格提示、只塞最近 3 条 preference、`quick_recap` ≤80 字、`find` 2-gram 检索、`summary` 只给计数不泄正文、mtime 重载、坏 JSON 跳过、日志不打印个人库正文 |
| `test_overview.py`（28 个） | 最近 7 天文件扫描（跳过 node_modules/.git/缓存）、DSH 会话目录名解码（`~XXXX`→Unicode、`-`→`/`）、会话临时目录过滤（可在 config 打开）、DSH 状态与心跳过期、TODO 线索（≤3 个/≤30 行）、HTML/JSON 写入与 HTML 转义、刷新节流与并发锁、`is_stale`、`text_summary` ≤500 字、AI 建议成功/失败回退、个人库正文不外发、Key 不进日志/HTML/JSON |

### 运行

```powershell
# 只跑新增的两个模块
python -m unittest pet.tests.test_personal pet.tests.test_overview -v

# 整套（不能破坏原有 35 个）
python -m unittest discover -s pet\tests -v
```

### 新增模块实际输出（2026-10-25，48 个全过）

```text
test_corrupt_journal_line_is_skipped (pet.tests.test_personal.PersonalLibraryTests.test_corrupt_journal_line_is_skipped) ... ok
test_disabled_library_does_nothing (pet.tests.test_personal.PersonalLibraryTests.test_disabled_library_does_nothing) ... ok
test_find_empty_query_returns_general_material (pet.tests.test_personal.PersonalLibraryTests.test_find_empty_query_returns_general_material) ... ok
test_find_excludes_pitfall_and_respects_limit (pet.tests.test_personal.PersonalLibraryTests.test_find_excludes_pitfall_and_respects_limit) ... ok
test_find_returns_relevant_entries (pet.tests.test_personal.PersonalLibraryTests.test_find_returns_relevant_entries) ... ok
test_loads_and_extracts_profile_fields (pet.tests.test_personal.PersonalLibraryTests.test_loads_and_extracts_profile_fields) ... ok
test_log_callback_failure_does_not_break_load (pet.tests.test_personal.PersonalLibraryTests.test_log_callback_failure_does_not_break_load) ... ok
test_logs_never_contain_personal_body (pet.tests.test_personal.PersonalLibraryTests.test_logs_never_contain_personal_body) ... ok
test_missing_library_degrades_without_crash (pet.tests.test_personal.PersonalLibraryTests.test_missing_library_degrades_without_crash) ... ok
test_never_writes_personal_library (pet.tests.test_personal.PersonalLibraryTests.test_never_writes_personal_library) ... ok
test_only_journal_partial_library_still_works (pet.tests.test_personal.PersonalLibraryTests.test_only_journal_partial_library_still_works) ... ok
test_prompt_block_contents_and_no_pitfall (pet.tests.test_personal.PersonalLibraryTests.test_prompt_block_contents_and_no_pitfall) ... ok
test_prompt_block_keeps_only_three_recent_preferences (pet.tests.test_personal.PersonalLibraryTests.test_prompt_block_keeps_only_three_recent_preferences) ... ok
test_prompt_block_truncates_to_max_chars (pet.tests.test_personal.PersonalLibraryTests.test_prompt_block_truncates_to_max_chars) ... ok
test_prompt_block_with_query_adds_related (pet.tests.test_personal.PersonalLibraryTests.test_prompt_block_with_query_adds_related) ... ok
test_quick_recap_length_and_content (pet.tests.test_personal.PersonalLibraryTests.test_quick_recap_length_and_content) ... ok
test_quick_recap_short_for_long_fields (pet.tests.test_personal.PersonalLibraryTests.test_quick_recap_short_for_long_fields) ... ok
test_reload_detects_mtime_change_only_when_needed (pet.tests.test_personal.PersonalLibraryTests.test_reload_detects_mtime_change_only_when_needed) ... ok
test_summary_after_reload_loaded_at_present (pet.tests.test_personal.PersonalLibraryTests.test_summary_after_reload_loaded_at_present) ... ok
test_summary_has_counts_without_body (pet.tests.test_personal.PersonalLibraryTests.test_summary_has_counts_without_body) ... ok
test_decode_degrades_on_weird_input (pet.tests.test_overview.DecodeSessionDirTests.test_decode_degrades_on_weird_input) ... ok
test_decode_plain_ascii_path (pet.tests.test_overview.DecodeSessionDirTests.test_decode_plain_ascii_path) ... ok
test_decode_unicode_and_drive (pet.tests.test_overview.DecodeSessionDirTests.test_decode_unicode_and_drive) ... ok
test_ai_disabled_in_config_does_not_call (pet.tests.test_overview.WorkOverviewTests.test_ai_disabled_in_config_does_not_call) ... ok
test_ai_suggestions_failure_falls_back_to_template (pet.tests.test_overview.WorkOverviewTests.test_ai_suggestions_failure_falls_back_to_template) ... ok
test_ai_suggestions_via_local_fake_server (pet.tests.test_overview.WorkOverviewTests.test_ai_suggestions_via_local_fake_server) ... ok
test_dsh_status_parsed (pet.tests.test_overview.WorkOverviewTests.test_dsh_status_parsed) ... ok
test_dsh_status_stale_heartbeat (pet.tests.test_overview.WorkOverviewTests.test_dsh_status_stale_heartbeat) ... ok
test_html_and_json_written (pet.tests.test_overview.WorkOverviewTests.test_html_and_json_written) ... ok
test_html_escapes_todo_content (pet.tests.test_overview.WorkOverviewTests.test_html_escapes_todo_content) ... ok
test_is_stale_after_old_generated_at (pet.tests.test_overview.WorkOverviewTests.test_is_stale_after_old_generated_at) ... ok
test_missing_personal_gives_note (pet.tests.test_overview.WorkOverviewTests.test_missing_personal_gives_note) ... ok
test_missing_sessions_status_and_roots_degrade (pet.tests.test_overview.WorkOverviewTests.test_missing_sessions_status_and_roots_degrade) ... ok
test_own_project_scanned_even_with_many_workspaces (pet.tests.test_overview.WorkOverviewTests.test_own_project_scanned_even_with_many_workspaces) ... ok
test_personal_summary_in_data_and_html (pet.tests.test_overview.WorkOverviewTests.test_personal_summary_in_data_and_html) ... ok
test_project_limit_respected (pet.tests.test_overview.WorkOverviewTests.test_project_limit_respected) ... ok
test_real_personal_library_integration (pet.tests.test_overview.WorkOverviewTests.test_real_personal_library_integration) ... ok
test_refresh_collects_recent_projects_and_skips_noise (pet.tests.test_overview.WorkOverviewTests.test_refresh_collects_recent_projects_and_skips_noise) ... ok
test_refresh_throttle_and_force (pet.tests.test_overview.WorkOverviewTests.test_refresh_throttle_and_force) ... ok
test_session_activity_decoded_and_filtered (pet.tests.test_overview.WorkOverviewTests.test_session_activity_decoded_and_filtered) ... ok
test_temp_sessions_included_when_configured (pet.tests.test_overview.WorkOverviewTests.test_temp_sessions_included_when_configured) ... ok
test_temp_workspace_sessions_filtered_by_default (pet.tests.test_overview.WorkOverviewTests.test_temp_workspace_sessions_filtered_by_default) ... ok
test_template_suggestions_without_key (pet.tests.test_overview.WorkOverviewTests.test_template_suggestions_without_key) ... ok
test_text_summary_before_refresh (pet.tests.test_overview.WorkOverviewTests.test_text_summary_before_refresh) ... ok
test_text_summary_does_not_include_personal_block (pet.tests.test_overview.WorkOverviewTests.test_text_summary_does_not_include_personal_block) ... ok
test_text_summary_length_and_content (pet.tests.test_overview.WorkOverviewTests.test_text_summary_length_and_content) ... ok
test_todo_files_scanned_limited_and_truncated (pet.tests.test_overview.WorkOverviewTests.test_todo_files_scanned_limited_and_truncated) ... ok
test_todo_missing_gives_hint (pet.tests.test_overview.WorkOverviewTests.test_todo_missing_gives_hint) ... ok

----------------------------------------------------------------------
Ran 48 tests in 2.001s

OK
```

### 整套实际输出（2026-10-25，122 个全过：原有 35 + 新增 48 + Agent 层 39）

```text
test_401_returns_friendly_error_without_key (test_agent.ApiErrorTests.test_401_returns_friendly_error_without_key) ... ok
test_bad_json_response (test_agent.ApiErrorTests.test_bad_json_response) ... ok
test_key_priority_config_env_file (test_agent.ApiErrorTests.test_key_priority_config_env_file) ... ok
test_missing_choices_response (test_agent.ApiErrorTests.test_missing_choices_response) ... ok
test_no_key_returns_error (test_agent.ApiErrorTests.test_no_key_returns_error) ... ok
test_timeout_returns_error (test_agent.ApiErrorTests.test_timeout_returns_error) ... ok
test_confirm_exception_treated_as_reject (test_agent.ConfirmTests.test_confirm_exception_treated_as_reject) ... ok
test_dangerous_command_rejected_without_confirm (test_agent.ConfirmTests.test_dangerous_command_rejected_without_confirm)
明显破坏性命令直接拒，不打扰主人确认。 ... ok
test_run_powershell_confirm_false_does_not_execute (test_agent.ConfirmTests.test_run_powershell_confirm_false_does_not_execute)
confirm 拒绝时命令绝不执行，模型收到拒绝结果。 ... ok
test_sensitive_command_rejected_without_confirm (test_agent.ConfirmTests.test_sensitive_command_rejected_without_confirm)
命令里提到敏感文件（Key/.env/id_rsa/*.pem）直接拒，不弹确认。 ... ok
test_shell_success_output (test_agent.ConfirmTests.test_shell_success_output)
确认后能真的执行无害命令，工作目录在项目根（这里临时目录）。 ... ok
test_shell_timeout_kills_process (test_agent.ConfirmTests.test_shell_timeout_kills_process)
命令超时：杀掉进程、返回 timeout，不卡死 run()。 ... ok
test_write_file_after_confirm_true (test_agent.ConfirmTests.test_write_file_after_confirm_true)
confirm 点头才写文件；args/preview 都给 confirm 看得到。 ... ok
test_write_file_confirm_none_rejected_end_to_end (test_agent.ConfirmTests.test_write_file_confirm_none_rejected_end_to_end)
没有确认弹窗时拒绝写文件，模型收到拒绝结果并继续回答。 ... ok
test_binary_files_rejected (test_agent.PathSafetyTests.test_binary_files_rejected) ... ok
test_default_allowed_roots_is_project_only (test_agent.PathSafetyTests.test_default_allowed_roots_is_project_only)
不覆盖 allowed_roots 时，默认白名单只有项目根；个人库要在配置里显式开放。 ... ok
test_list_dir_and_search_files (test_agent.PathSafetyTests.test_list_dir_and_search_files) ... ok
test_outside_allowed_root_rejected (test_agent.PathSafetyTests.test_outside_allowed_root_rejected) ... ok
test_path_traversal_rejected (test_agent.PathSafetyTests.test_path_traversal_rejected) ... ok
test_read_file_basic_and_truncated (test_agent.PathSafetyTests.test_read_file_basic_and_truncated) ... ok
test_secret_files_rejected (test_agent.PathSafetyTests.test_secret_files_rejected) ... ok
test_too_large_read_rejected (test_agent.PathSafetyTests.test_too_large_read_rejected) ... ok
test_write_file_limits (test_agent.PathSafetyTests.test_write_file_limits) ... ok
test_dsh_status_adapter (test_agent.ReadonlyToolTests.test_dsh_status_adapter) ... ok
test_personal_adapter (test_agent.ReadonlyToolTests.test_personal_adapter) ... ok
test_prompt_block_legacy_no_arg_signature (test_agent.ReadonlyToolTests.test_prompt_block_legacy_no_arg_signature) ... ok
test_run_works_when_personal_and_overview_missing (test_agent.ReadonlyToolTests.test_run_works_when_personal_and_overview_missing)
两个模块都没接上时，run() 仍能正常走完一轮并回话。 ... ok
test_work_overview_adapter (test_agent.ReadonlyToolTests.test_work_overview_adapter) ... ok
test_key_not_echoed_in_reply (test_agent.RedactionTests.test_key_not_echoed_in_reply)
模型万一复述了 Key，最终回复里也要抹掉。 ... ok
test_key_not_in_system_prompt (test_agent.RedactionTests.test_key_not_in_system_prompt) ... ok
test_tool_output_redacts_key (test_agent.RedactionTests.test_tool_output_redacts_key)
工具读到的内容里就算有 Key，也不能原样进模型上下文/步骤。 ... ok
test_answer_without_tool (test_agent.RunLoopTests.test_answer_without_tool)
模型直接回答：不调工具，source=agent，steps 为空。 ... ok
test_empty_user_text_no_request (test_agent.RunLoopTests.test_empty_user_text_no_request)
空输入：直接返回 error，不发请求。 ... ok
test_max_steps_stops_at_six (test_agent.RunLoopTests.test_max_steps_stops_at_six)
模型一直要工具：第 6 步停下，给可读失败说明。 ... ok
test_multiple_tool_calls_in_one_round (test_agent.RunLoopTests.test_multiple_tool_calls_in_one_round)
一轮里多个 tool_calls：都要执行，且按返回顺序进 steps。 ... ok
test_on_step_callback_error_does_not_break_run (test_agent.RunLoopTests.test_on_step_callback_error_does_not_break_run)
UI 的 on_step 回调抛异常也不能打断 agent。 ... ok
test_two_round_tool_call_then_reply (test_agent.RunLoopTests.test_two_round_tool_call_then_reply)
第一轮 tool_calls、第二轮最终回复：顺序、steps、reply 都对。 ... ok
test_mutating_tools_are_marked (test_agent.ToolSpecTests.test_mutating_tools_are_marked) ... ok
test_tool_specs_are_openai_compatible (test_agent.ToolSpecTests.test_tool_specs_are_openai_compatible) ... ok
test_required_interface (test_brain.InterfaceTests.test_required_interface)
Lead 的 app.py 依赖的接口签名都在。 ... ok
test_401_falls_back_to_offline_error_without_leaking_key (test_brain.LLMClientTests.test_401_falls_back_to_offline_error_without_leaking_key)
401：Key 无效 → offline-error，日志里不能出现 Key。 ... ok
test_429_exhausted_falls_back (test_brain.LLMClientTests.test_429_exhausted_falls_back)
429 用尽重试 → offline-error（不抛异常给界面）。 ... ok
test_429_retry_then_success (test_brain.LLMClientTests.test_429_retry_then_success)
429 限流：允许重试一次，第二次成功。 ... ok
test_bad_response_falls_back (test_brain.LLMClientTests.test_bad_response_falls_back)
接口返回坏 JSON / 空 choices → offline-error。 ... ok
test_key_priority_config_env_file (test_brain.LLMClientTests.test_key_priority_config_env_file)
Key 优先级：config → 环境变量 DFY_API_KEY → pet/.secret_api_key。 ... ok
test_llm_options_defaults_and_string_values (test_brain.LLMClientTests.test_llm_options_defaults_and_string_values)
config 里缺字段用默认值；界面存的字符串数字也能解析；非法值回默认。 ... ok
test_success_path_llm_reply (test_brain.LLMClientTests.test_success_path_llm_reply)
正常回复：走 /chat/completions，返回 llm，并写入记忆。 ... ok
test_test_connection_ok_and_failures (test_brain.LLMClientTests.test_test_connection_ok_and_failures)
设置窗「测试连接」：成功/401/无 Key 三种人话，都不含 Key。 ... ok
test_timeout_falls_back_to_offline_error (test_brain.LLMClientTests.test_timeout_falls_back_to_offline_error)
超时：0.2 秒超时、服务端睡 1.2 秒 → offline-error，且不拖太久。 ... ok
test_app_style_direct_memory_access (test_brain.MemoryTests.test_app_style_direct_memory_access)
兼容 app.py 设置窗直接写 brain.memory["user_name"] 的旧习惯。 ... ok
test_corrupt_memory_json_self_heals (test_brain.MemoryTests.test_corrupt_memory_json_self_heals)
memory.json 半截/乱码时不崩，坏文件留档，记忆按空处理。 ... ok
test_history_limit_60 (test_brain.MemoryTests.test_history_limit_60)
对话历史只保留最近 60 条。 ... ok
test_memory_summary_initial (test_brain.MemoryTests.test_memory_summary_initial) ... ok
test_memory_write_reload_and_retrieval (test_brain.MemoryTests.test_memory_write_reload_and_retrieval)
事实/称呼写入后能重载，并能按新输入检索到。 ... ok
test_old_memory_json_self_heals (test_brain.MemoryTests.test_old_memory_json_self_heals)
旧格式 memory.json（facts 是字符串、无 version）能读、能升级、不丢数据。 ... ok
test_disabled_model_ignores_stale_key (test_brain.OfflineAndPromptTests.test_disabled_model_ignores_stale_key) ... ok
test_offline_rules (test_brain.OfflineAndPromptTests.test_offline_rules)
没启用模型：命中关键词用对应台词，没命中用兜底台词，不发网络请求。 ... ok
test_status_text_variants (test_brain.OfflineAndPromptTests.test_status_text_variants) ... ok
test_system_prompt_contains_memory (test_brain.OfflineAndPromptTests.test_system_prompt_contains_memory)
system prompt 里要有最近对话 + 相关记忆，且不泄漏 Key。 ... ok
test_line_for_dsh_state (test_brain.ProactiveAndDSHTests.test_line_for_dsh_state)
running/success/failure/approval 给候选台词，idle/未知返回 None。 ... ok
test_proactive_contexts (test_brain.ProactiveAndDSHTests.test_proactive_contexts)
久未互动 / 工作状态 / 有上文时都能生成一句人话。 ... ok
test_proactive_llm_failure_uses_offline (test_brain.ProactiveAndDSHTests.test_proactive_llm_failure_uses_offline) ... ok
test_proactive_offline_dedupes_within_20 (test_brain.ProactiveAndDSHTests.test_proactive_offline_dedupes_within_20)
离线主动搭话：按时段模板，最近 20 条内不重复轰炸。 ... ok
test_proactive_with_llm_and_repeat_fallback (test_brain.ProactiveAndDSHTests.test_proactive_with_llm_and_repeat_fallback)
有模型时用模型生成；模型又返回重复句时退回离线模板。 ... ok
test_decode_degrades_on_weird_input (test_overview.DecodeSessionDirTests.test_decode_degrades_on_weird_input) ... ok
test_decode_plain_ascii_path (test_overview.DecodeSessionDirTests.test_decode_plain_ascii_path) ... ok
test_decode_unicode_and_drive (test_overview.DecodeSessionDirTests.test_decode_unicode_and_drive) ... ok
test_ai_disabled_in_config_does_not_call (test_overview.WorkOverviewTests.test_ai_disabled_in_config_does_not_call) ... ok
test_ai_suggestions_failure_falls_back_to_template (test_overview.WorkOverviewTests.test_ai_suggestions_failure_falls_back_to_template) ... ok
test_ai_suggestions_via_local_fake_server (test_overview.WorkOverviewTests.test_ai_suggestions_via_local_fake_server) ... ok
test_dsh_status_parsed (test_overview.WorkOverviewTests.test_dsh_status_parsed) ... ok
test_dsh_status_stale_heartbeat (test_overview.WorkOverviewTests.test_dsh_status_stale_heartbeat) ... ok
test_html_and_json_written (test_overview.WorkOverviewTests.test_html_and_json_written) ... ok
test_html_escapes_todo_content (test_overview.WorkOverviewTests.test_html_escapes_todo_content) ... ok
test_is_stale_after_old_generated_at (test_overview.WorkOverviewTests.test_is_stale_after_old_generated_at) ... ok
test_missing_personal_gives_note (test_overview.WorkOverviewTests.test_missing_personal_gives_note) ... ok
test_missing_sessions_status_and_roots_degrade (test_overview.WorkOverviewTests.test_missing_sessions_status_and_roots_degrade) ... ok
test_own_project_scanned_even_with_many_workspaces (test_overview.WorkOverviewTests.test_own_project_scanned_even_with_many_workspaces) ... ok
test_personal_summary_in_data_and_html (test_overview.WorkOverviewTests.test_personal_summary_in_data_and_html) ... ok
test_project_limit_respected (test_overview.WorkOverviewTests.test_project_limit_respected) ... ok
test_real_personal_library_integration (test_overview.WorkOverviewTests.test_real_personal_library_integration) ... ok
test_refresh_collects_recent_projects_and_skips_noise (test_overview.WorkOverviewTests.test_refresh_collects_recent_projects_and_skips_noise) ... ok
test_refresh_throttle_and_force (test_overview.WorkOverviewTests.test_refresh_throttle_and_force) ... ok
test_session_activity_decoded_and_filtered (test_overview.WorkOverviewTests.test_session_activity_decoded_and_filtered) ... ok
test_temp_sessions_included_when_configured (test_overview.WorkOverviewTests.test_temp_sessions_included_when_configured) ... ok
test_temp_workspace_sessions_filtered_by_default (test_overview.WorkOverviewTests.test_temp_workspace_sessions_filtered_by_default) ... ok
test_template_suggestions_without_key (test_overview.WorkOverviewTests.test_template_suggestions_without_key) ... ok
test_text_summary_before_refresh (test_overview.WorkOverviewTests.test_text_summary_before_refresh) ... ok
test_text_summary_does_not_include_personal_block (test_overview.WorkOverviewTests.test_text_summary_does_not_include_personal_block) ... ok
test_text_summary_length_and_content (test_overview.WorkOverviewTests.test_text_summary_length_and_content) ... ok
test_todo_files_scanned_limited_and_truncated (test_overview.WorkOverviewTests.test_todo_files_scanned_limited_and_truncated) ... ok
test_todo_missing_gives_hint (test_overview.WorkOverviewTests.test_todo_missing_gives_hint) ... ok
test_corrupt_journal_line_is_skipped (test_personal.PersonalLibraryTests.test_corrupt_journal_line_is_skipped) ... ok
test_disabled_library_does_nothing (test_personal.PersonalLibraryTests.test_disabled_library_does_nothing) ... ok
test_find_empty_query_returns_general_material (test_personal.PersonalLibraryTests.test_find_empty_query_returns_general_material) ... ok
test_find_excludes_pitfall_and_respects_limit (test_personal.PersonalLibraryTests.test_find_excludes_pitfall_and_respects_limit) ... ok
test_find_returns_relevant_entries (test_personal.PersonalLibraryTests.test_find_returns_relevant_entries) ... ok
test_loads_and_extracts_profile_fields (test_personal.PersonalLibraryTests.test_loads_and_extracts_profile_fields) ... ok
test_log_callback_failure_does_not_break_load (test_personal.PersonalLibraryTests.test_log_callback_failure_does_not_break_load) ... ok
test_logs_never_contain_personal_body (test_personal.PersonalLibraryTests.test_logs_never_contain_personal_body) ... ok
test_missing_library_degrades_without_crash (test_personal.PersonalLibraryTests.test_missing_library_degrades_without_crash) ... ok
test_never_writes_personal_library (test_personal.PersonalLibraryTests.test_never_writes_personal_library) ... ok
test_only_journal_partial_library_still_works (test_personal.PersonalLibraryTests.test_only_journal_partial_library_still_works) ... ok
test_prompt_block_contents_and_no_pitfall (test_personal.PersonalLibraryTests.test_prompt_block_contents_and_no_pitfall) ... ok
test_prompt_block_keeps_only_three_recent_preferences (test_personal.PersonalLibraryTests.test_prompt_block_keeps_only_three_recent_preferences) ... ok
test_prompt_block_truncates_to_max_chars (test_personal.PersonalLibraryTests.test_prompt_block_truncates_to_max_chars) ... ok
test_prompt_block_with_query_adds_related (test_personal.PersonalLibraryTests.test_prompt_block_with_query_adds_related) ... ok
test_quick_recap_length_and_content (test_personal.PersonalLibraryTests.test_quick_recap_length_and_content) ... ok
test_quick_recap_short_for_long_fields (test_personal.PersonalLibraryTests.test_quick_recap_short_for_long_fields) ... ok
test_reload_detects_mtime_change_only_when_needed (test_personal.PersonalLibraryTests.test_reload_detects_mtime_change_only_when_needed) ... ok
test_summary_after_reload_loaded_at_present (test_personal.PersonalLibraryTests.test_summary_after_reload_loaded_at_present) ... ok
test_summary_has_counts_without_body (test_personal.PersonalLibraryTests.test_summary_has_counts_without_body) ... ok
test_connect_emits_once_then_quiet (test_status_client.DshStatusWatcherTests.test_connect_emits_once_then_quiet) ... ok
test_corrupt_json_does_not_crash (test_status_client.DshStatusWatcherTests.test_corrupt_json_does_not_crash) ... ok
test_disconnect_when_file_removed (test_status_client.DshStatusWatcherTests.test_disconnect_when_file_removed) ... ok
test_invalid_state_ignored (test_status_client.DshStatusWatcherTests.test_invalid_state_ignored) ... ok
test_iso_timestamp_supported (test_status_client.DshStatusWatcherTests.test_iso_timestamp_supported) ... ok
test_missing_file_is_silent (test_status_client.DshStatusWatcherTests.test_missing_file_is_silent) ... ok
test_same_state_but_new_done_count_emits (test_status_client.DshStatusWatcherTests.test_same_state_but_new_done_count_emits) ... ok
test_snapshot_and_describe (test_status_client.DshStatusWatcherTests.test_snapshot_and_describe) ... ok
test_stale_heartbeat_disconnects (test_status_client.DshStatusWatcherTests.test_stale_heartbeat_disconnects) ... ok
test_state_transitions_emit_events (test_status_client.DshStatusWatcherTests.test_state_transitions_emit_events) ... ok

----------------------------------------------------------------------
Ran 122 tests in 10.357s

OK
```

补充：真实环境手工跑过 `python tools/build_overview.py --force --print`，
生成 `pet/overview.json` 与 `docs/工作总览.html`，`text_summary` 长度 352 字
（AI 建议来源 `ai`，只有 Key 可用时才调用；默认 10 分钟节流，刷新失败静默回退模板建议）。
