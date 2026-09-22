# coding: utf-8
"""
CapsWriter Offline 服务端主程序门面类 (Facade)

采用外观模式统一管理进程管理器 (ProcessManager) 和网络管理器 (SocketManager)。
该类是整个服务端应用的中心指挥部，负责初始化生命周期、托盘图标、
并协调子进程与 WebSocket 服务的启动与退出。
"""

import os
import asyncio
import queue
import threading
from pathlib import Path
from config_server import ServerConfig as Config, __version__
from .state import ServerState, console
from core.tools.signal_handler import register_signal
from .worker.process_manager import ProcessManager
from .connection.server_manager import SocketManager
from .ui.tray_manager import TrayManager
from . import logger

class CapsWriterServer:
    """
    CapsWriter 服务端外观类
    
    管理的外部接口极其简洁：start()。
    """
    def __init__(self):
        # 确保正确的工作目录
        self.base_dir = Path(__file__).parents[2]
        os.chdir(self.base_dir)

        # 初始化事件循环
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # 初始化状态容器
        self.state = ServerState(app=self)

        # 基本配置与组件实例化
        self.process_manager = ProcessManager(self)
        self.socket_manager = SocketManager(self)
        self.tray_manager = TrayManager(self)

        self.version = __version__
        self.is_alive = False
        self._owner_thread = threading.get_ident()
        self._stop_requested = threading.Event()
        self._cleaned_up = False
        import config_server
        from config_templates import config_server_template
        from core.config_reload import ConfigReloader, SERVER_LIVE
        self.config_reload = ConfigReloader(
            self.base_dir / 'config_server.py', config_server, config_server_template,
            'ServerConfig', SERVER_LIVE, self._report_config,
        )

    def _report_config(self, message):
        logger.info(message, extra={'console_handled': True})
        console.print(message, markup=False)

    def apply_config_reload(self):
        # AudioCache snapshots these settings on the same event loop at admission.
        changed = self.config_reload.apply()
        if changed:
            self._report_config('Configuration applied to new tasks: ' + ', '.join(changed))


    def _print_banner(self):
        """打印启动信息"""
        console.line(2)
        console.rule('[bold #d55252]CapsWriter Offline Server[/]'); console.line()
        console.print(f'版本：[bold green]{self.version}[/]', end='\n\n')
        console.print(f'项目地址：[cyan underline]https://github.com/HaujetZhao/CapsWriter-Offline', end='\n\n')
        console.print(f'当前基文件夹：[cyan underline]{self.base_dir}[/]', end='\n\n')
        console.print(f'绑定的服务地址：[cyan underline]{Config.addr}:{Config.port}[/]', end='\n\n')

    def stop(self):
        """Request shutdown on the loop owner; reap processes after the loop drains."""
        self._stop_requested.set()
        if threading.get_ident() != self._owner_thread:
            if self.loop.is_running():
                try:
                    self.loop.call_soon_threadsafe(self.stop)
                except RuntimeError:
                    pass  # The owner is already closing the loop.
            return
        self.is_alive = False
        if self.loop.is_running():
            self.socket_manager.stop()

    def _cleanup(self):
        """Called by start's finally, never reentrantly from a signal/tray callback."""
        self.is_alive = False
        if self._cleaned_up:
            return
        self._cleaned_up = True

        logger.info("=" * 50)
        logger.info("开始清理服务端资源...")

        try:
            self.state.queue_out.put_nowait(None)
        except (queue.Full, OSError, EOFError, ValueError) as exc:
            logger.debug('Result shutdown signal unavailable: %s', type(exc).__name__)

        # A broken queue/component must not skip the remaining cleanup owners.
        for name, component in (('network', self.socket_manager),
                                ('worker', self.process_manager),
                                ('tray', self.tray_manager)):
            try:
                component.stop()
            except Exception as exc:
                logger.error('Server cleanup failed: component=%s error=%s', name, type(exc).__name__)

        logger.info("服务端资源清理完成")
        console.print('[green4]再见！')


    def start(self):
        """
        同步启动服务端 (主入口)
        
        注册信号处理、拉起子进程并进入网络服务监听循环。
        """
        # 防连续触发
        if self.is_alive: return
        self._owner_thread = threading.get_ident()
        self._stop_requested = threading.Event()
        self._cleaned_up = False

        # 安全配置必须在启动托盘和模型子进程前通过校验
        try:
            self.socket_manager.prepare()
        except ValueError as exc:
            logger.critical(f"服务端网络配置无效: {exc}")
            console.print(f'[bold red]服务端网络配置无效：{exc}[/bold red]')
            return

        self.is_alive = True

        # 注册退出信号处理
        register_signal(self.stop)

        try:
            self.tray_manager.start()
            self._print_banner()
            self.process_manager.start()
            if self.is_alive and not self._stop_requested.is_set():
                if hasattr(self, 'config_reload'):
                    self.config_reload.task = self.loop.create_task(
                        self.config_reload.watch(self.apply_config_reload))
                self.loop.run_until_complete(self.socket_manager.start())
        finally:
            if hasattr(self, 'config_reload'):
                self.loop.run_until_complete(self.config_reload.close())
            # Sender failure ends the listener; release model processes and tray too.
            self._cleanup()
            self.loop.close()
