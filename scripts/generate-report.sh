#!/usr/bin/env bash
set -euo pipefail

REQUIRED_ARGS=()
REQUIRED_ENV_VARS=()
REQUIRED_PROGRAMS=("docker")

usage() {
  cat << 'EOF'
Usage: scripts/generate-report.sh

Exports the current indexed_documents table to a timestamped text file under
reports/.

External tools:
  docker

Examples:
  scripts/generate-report.sh
EOF
}

check_requirements() {
  local -r provided_arg_count=$1
  local missing=0
  local env_var
  local program

  if [ ${#REQUIRED_ARGS[@]} -gt 0 ] && [ "$provided_arg_count" -lt ${#REQUIRED_ARGS[@]} ]; then
    printf 'Error: Expected %s arguments (%s) but received %s.\n' \
      ${#REQUIRED_ARGS[@]} "${REQUIRED_ARGS[*]}" "$provided_arg_count" >&2
    missing=1
  fi

  for env_var in "${REQUIRED_ENV_VARS[@]-}"; do
    [ -z "$env_var" ] && continue
    if [ -z "${!env_var:-}" ]; then
      printf 'Error: Missing required environment variable %s. Please set it before rerunning.\n' "$env_var" >&2
      missing=1
    fi
  done

  for program in "${REQUIRED_PROGRAMS[@]-}"; do
    [ -z "$program" ] && continue
    if ! command -v "$program" > /dev/null 2>&1; then
      printf 'Error: Required program %s is not installed or not on PATH. Please install it first.\n' "$program" >&2
      missing=1
    fi
  done

  if ! docker compose version > /dev/null 2>&1; then
    printf 'Error: docker compose is not available. Please install or enable Docker Compose first.\n' >&2
    missing=1
  fi

  if [ "$missing" -ne 0 ]; then
    printf '\n' >&2
    usage >&2
    return 1
  fi
}

main() {
  check_requirements "$#" || exit 1

  local -r report_dir="reports"
  local -r timestamp="$(date +%Y%m%d-%H%M%S)"
  local -r output_path="${report_dir}/indexed-documents-${timestamp}.txt"

  mkdir -p "$report_dir"

  docker compose exec -T sec-filings-rag-postgres \
    psql -U sec_filings_rag -d sec_filings_rag -c \
    "select * from indexed_documents order by created_at desc;" \
    > "$output_path"

  printf 'Report written to %s\n' "$output_path"
}

main "$@"
