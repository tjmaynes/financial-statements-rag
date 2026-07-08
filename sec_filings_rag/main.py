from __future__ import annotations

import uvicorn

from sec_filings_rag.web.app import create_app

app = create_app()


def main() -> None:
    uvicorn.run("sec_filings_rag.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
