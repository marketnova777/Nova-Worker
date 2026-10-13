#!/usr/bin/env python3
"""Nova worker entrypoint.

  python run_cycle.py --dry-run   # compute the plan, write nothing
  python run_cycle.py             # full cycle with batched delta writes
"""
import sys
from nova.cycle import run

if __name__ == "__main__":
    run(dry="--dry-run" in sys.argv or "-n" in sys.argv)
