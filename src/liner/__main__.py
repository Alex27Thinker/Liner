"""Allow `python -m liner` as an alias for the `liner` command."""

from liner.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
