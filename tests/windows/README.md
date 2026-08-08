# Windows 实机测试

放置需要真实 Windows 桌面、麦克风、全局按键、托盘、DPI 或 GPU 后端的测试。
该目录会自动添加 `windows` 标记，默认 `pytest` 和托管 CI 不执行。

需要模型、网络或人工观察的用例还应显式添加 `@pytest.mark.manual`。
