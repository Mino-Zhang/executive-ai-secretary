#!/usr/bin/env sh
set -eu

password_file="${POSTGRES_BACKUP_PASSWORD_FILE:-/run/secrets/postgres_backup_password}"
[ -s "${password_file}" ] || { printf 'backup database password is missing\n' >&2; exit 66; }
[ "$#" -gt 0 ] || { printf 'a PostgreSQL client command is required\n' >&2; exit 64; }

export PGPASSWORD="$(cat "${password_file}")"
exec "$@"
