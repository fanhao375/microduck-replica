"""Studio 的启动入口：python3 -I run.py [参数]

为什么要这个文件：用 -I（隔离模式）启动时，Python 不会把当前目录、用户目录里的包加进来，
这正是我们要的（只用 deps/ 里钉死版本的依赖），但 `-m studio` 也就找不到包了。
所以这里明确把 deps/ 和本目录放到最前面，再进 studio。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "deps"), HERE]

from studio.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
