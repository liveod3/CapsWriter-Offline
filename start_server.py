# coding: utf-8
from multiprocessing import freeze_support

if __name__ == '__main__':
    # Enable multiprocessing in PyInstaller builds.
    freeze_support()
    # Spawn must reach the configuration bootstrap before importing the server.
    from core.server.app import CapsWriterServer
    
    # Start the application facade.
    # CapsWriterServer owns environment initialization.
    CapsWriterServer().start()
