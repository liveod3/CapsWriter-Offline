# coding: utf-8
from multiprocessing import freeze_support

if __name__ == '__main__':
    # 启用对 PyInstaller 打包后的多进程支持
    freeze_support()
    # Spawn must reach the configuration bootstrap before importing the server.
    from core.server.app import CapsWriterServer
    
    # 直接实例化并启动门面类即可
    # 环境初始化职责已下放至 CapsWriterServer
    CapsWriterServer().start()
