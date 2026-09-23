# Codex CLI 网页包装

当前 WebUI 只使用 `web.py`，由已有的 dashboard 入口挂载 `/api/codex`。无需执行 `python -m homework_service`。配置继续读取 `homework.yml`，沿用用户的 `workspace` 和 `codex_model`。

- 会话独立于作业，只包装 codex exec / exec resume。
- 所有执行使用配置的工作目录，不生成课程目录、作业目录或资料快照。
- 原生历史从当前 CODEX_HOME 的 sessions 读取，展示配置目录内的会话。
- 页面支持更换目录、创建与查看会话、续聊、切换模型和停止执行。
- 旧记录与用户文件保留；旧作业管理 HTTP 入口在当前部署中已停用。
- 更改目录时会固定旧记录位置，避免重启后丢失历史。重启 dashboard 会中断正在执行的 CLI。

原有 app.py/source.py 仅保留用于兼容旧记录与历史测试，不属于当前 WebUI 的执行流程。
