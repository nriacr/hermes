import sys

from .service import run_service


def main() -> int:
    return run_service()


if __name__ == "__main__":
    sys.exit(main())
