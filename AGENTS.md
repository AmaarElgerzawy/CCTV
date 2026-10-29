# Rules for this project

- Never create a file that was not explicitly requested. No helper, check, cleanup, debug, flake or one-off scripts in the project folder. Temporary scripts go in scratch/ (git-ignored) and are deleted before you finish.
- Never open or print .env or data/dvr.db. Never log or return passwords, the encryption key, RTSP URLs, FilePath or host names in error messages.
- Every coroutine must be awaited or scheduled with asyncio.create_task. After every change run: python -m pyflakes *.py (must print nothing) and python -W error::RuntimeWarning -m unittest discover -p "test_*.py" (must be OK).
- Every bug fix and every new feature needs tests that go through the real code path (the real app lifespan, the real registry in a temporary DATA_DIR, mocks only at the httpx transport or socket level). The test count must go up, and a bug-fix test must fail without the fix.
- Do not claim something is done unless you ran the commands above and quote their last lines.