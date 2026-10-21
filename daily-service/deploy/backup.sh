#!/usr/bin/env sh
set -eu
backup_dir="${BACKUP_DIR:-./backups}"
mkdir -p "$backup_dir"
umask 077
backup_file="$backup_dir/database-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
plain_file="$backup_file.tmp"
trap 'rm -f "$plain_file"' EXIT INT TERM
docker compose exec -T db pg_dump -U vypryamis --no-owner vypryamis > "$plain_file"
gzip -c "$plain_file" > "$backup_file"
test -s "$backup_file"
printf '%s\n' "$backup_file"
