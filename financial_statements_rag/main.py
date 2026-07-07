from __future__ import annotations

import uvicorn

from financial_statements_rag.web.app import create_app

app = create_app()


def main() -> None:
    uvicorn.run("financial_statements_rag.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
