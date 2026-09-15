# Windows 实机测试

放置需要真实 Windows 桌面、麦克风、全局按键、托盘、DPI 或 GPU 后端的测试。
该目录会自动添加 `windows` 标记，默认 `pytest` 和托管 CI 不执行。

需要模型、网络或人工观察的用例还应显式添加 `@pytest.mark.manual`。

`test_native_menu.py` 只创建和释放原生菜单/窗口句柄，检查文字、位图、子菜单 Tooltip 映射，
并验证接入 Tooltip 后仍保留默认窗口消息处理，
不显示菜单、不读取用户输入或启动麦克风。可单独运行：

```powershell
python -m pytest tests/windows/test_native_menu.py -m windows
```

它不覆盖实际悬停、混合 DPI、深色主题、键盘导航、焦点切换与打包运行。
