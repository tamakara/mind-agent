import argparse
from collections.abc import Sequence

from mock_oa_service import __version__
from mock_oa_service.config import MockOASettings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mock_oa_service",
        description="Run the WorkHub Mock OA service.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument("--serve", action="store_true", help="Run the HTTP service.")
    args = parser.parse_args(argv)
    if args.serve:
        import uvicorn

        settings = MockOASettings()
        uvicorn.run(
            "mock_oa_service.app:app", host=settings.host, port=settings.port, log_config=None
        )
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
