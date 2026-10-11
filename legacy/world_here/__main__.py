import argparse
import asyncio

from . import config as C
from .hub import main

parser = argparse.ArgumentParser(prog="python -m world_here")
parser.add_argument("--host", default=C.HOST)
parser.add_argument("--port", type=int, default=C.PORT)
args = parser.parse_args()

try:
    asyncio.run(main(args.host, args.port))
except KeyboardInterrupt:
    pass
