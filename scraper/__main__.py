import sys

from .run import cli

raise SystemExit(cli(sys.argv[1:]))
