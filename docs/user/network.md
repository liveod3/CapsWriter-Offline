# 连接局域网服务端

默认 `network_mode='local'` 只允许回环监听，同一台电脑上的客户端可以连接。不要仅把 `addr` 改成 `0.0.0.0` 来开放服务。

## 启用 LAN 认证

1. 生成至少 32 个字符的随机令牌：

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. 在服务端启动环境中设置 `CAPSWRITER_AUTH_TOKEN`。在本机操作，不把真实令牌写进仓库、聊天或快捷方式参数：

   ```powershell
   $env:CAPSWRITER_AUTH_TOKEN = '<your-generated-token>'
   ```

3. 修改服务端 `ServerConfig`：

   ```python
   network_mode = 'lan'
   addr = '0.0.0.0'
   ```

4. 在每台客户端的启动环境中设置相同令牌，将 `ClientConfig.addr` 改为服务端地址，并核对端口。
5. 重启服务端和客户端，再测试连接。

令牌缺失或太短时，服务端在加载模型前拒绝启动。握手缺少令牌或令牌错误时返回 HTTP 401。轮换令牌需要同时更新所有参与进程并重启。

## 配置 TLS

Bearer 认证不加密数据。普通 `ws://` 会明文传输令牌和音频；跨不可信网络时，应配置 TLS 或使用受信任的 TLS 反向代理。

直接 TLS 的服务端设置：

```python
tls_certfile = 'path/to/certificate.pem'
tls_keyfile = 'path/to/private-key.pem'
```

客户端设置：

```python
use_tls = True
tls_ca_file = ''
```

公共 CA 证书通常可保留空的 `tls_ca_file`；私有 CA 或自签名证书需要配置受信任证书路径。客户端使用 `wss://` 并校验证书链与主机名，不要关闭校验来掩盖地址或证书错误。

网络、认证和 TLS 均属于需重启的资源设置。完成当前任务后再重启。
