# Codex 网页包装验证

```sh
python -m unittest tests.test_codex_web tests.test_homework tests.test_dashboard_gateway -v
```

当前入口使用 `/api/codex`，不依赖独立 Homework 进程。验证新建与续聊的工作目录、模型传递、停止进程、原生会话读取、目录变更后的记录位置以及访问鉴权。

测试 CLI 不访问模型，使用临时目录中的 AGENTS.md 验证工作目录传递；真实模型读取指令的效果应另行验证。旧作业服务测试仅覆盖兼容实现，不表示当前页面仍提供目录分配、资料快照或成果管理。
