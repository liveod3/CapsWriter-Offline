import sys
import os
from os.path import dirname, join, exists

# Add the executable directory to the module search path.
# Resolve copied source files, including configuration and core modules.
executable_dir = dirname(sys.executable)
sys.path.insert(0, executable_dir)

# PyInstaller places third-party DLL and PYD dependencies in internal/.
# Add internal/ to the search path so Python can load these dependencies.
internal_dir = join(executable_dir, 'internal')
if exists(internal_dir):
    sys.path.insert(0, internal_dir)
