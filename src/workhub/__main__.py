import uvicorn

from workhub.config import WorkHubSettings


def main() -> None:
    settings = WorkHubSettings()
    uvicorn.run(
        "workhub.app:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
