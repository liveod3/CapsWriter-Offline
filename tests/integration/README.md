# 集成测试

放置跨协议、连接、队列或多个组件协作的测试。该目录会自动添加 `integration` 标记，
默认 `pytest` 不执行；使用 `python -m pytest -m integration` 显式运行。

测试不得依赖真实 API Key、个人音频或已安装的大模型。
