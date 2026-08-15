# 配置模板

本目录保存受 Git 跟踪的 `config_client_template.py` 和
`config_server_template.py` 默认配置模板。程序不会直接读取模板，而是读取仓库
根目录的 `config_client.py` 和 `config_server.py`；这两个本机文件被
`.gitignore` 忽略，不会进入提交。

首次从源码运行时，在仓库根目录执行：

```powershell
if (!(Test-Path config_client.py)) { Copy-Item config_templates/config_client_template.py config_client.py }
if (!(Test-Path config_server.py)) { Copy-Item config_templates/config_server_template.py config_server.py }
```

上面的命令会将模板**复制**到仓库根目录，并在复制时去掉 `_template` 后缀；不要
移动或直接重命名本目录中的模板，否则 Git 工作区会再次缺少规范源。只在目标文件
不存在时初始化。升级后如果模板出现新字段、字段重命名或结构变化，
请手动将这些变化合并到本机配置，同时保留自己的设备、模型、快捷键和功能开关。
可以用以下命令查看差异：

```powershell
git diff --no-index -- config_templates/config_client_template.py config_client.py
git diff --no-index -- config_templates/config_server_template.py config_server.py
```

维护配置规范时，应修改本目录中的模板；如当前工作区已有根配置，还应同步必要的
结构变化以便本地运行，但不得用模板整体覆盖用户文件。PyInstaller 发行包同样从
本目录复制默认配置，避免把开发机设置或敏感信息带入发布物。
