ARG PYTHON_VERSION=3.14.6
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY financial_statements_rag ./financial_statements_rag

RUN python -m pip install --upgrade pip \
    && python -m pip install .

EXPOSE 8000

CMD ["uvicorn", "financial_statements_rag.main:app", "--host", "0.0.0.0", "--port", "8000"]
