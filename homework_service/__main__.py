import argparse
import os
from contextlib import contextmanager
from aiohttp import web
from .config import Settings
from .app import create_app


@contextmanager
def service_lock(root):
    root.mkdir(parents=True,exist_ok=True)
    with (root/'service.lock').open('a+b') as handle:
        if os.name=='nt':
            import msvcrt
            if handle.tell()==0:handle.write(b'0');handle.flush()
            handle.seek(0)
            try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise RuntimeError('该 Homework 工作区已有服务运行') from None
        else:
            import fcntl
            try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise RuntimeError('该 Homework 工作区已有服务运行') from None
        try:yield
        finally:
            if os.name=='nt':handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def main():
    parser=argparse.ArgumentParser(description='独立作业与 Agent 管理服务')
    parser.add_argument('--config',default='homework.yml')
    args=parser.parse_args();settings=Settings.load(args.config)
    with service_lock(settings.state):
        web.run_app(create_app(settings),host=settings.host,port=settings.port)


if __name__=='__main__':main()
