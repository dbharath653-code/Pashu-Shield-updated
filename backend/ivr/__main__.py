"""Allow `python -m ivr` (alias of `python -m ivr.worker`)."""
from .worker import main

if __name__ == "__main__":
    raise SystemExit(main())
