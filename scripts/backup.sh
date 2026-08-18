#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
instance_dir="${project_dir}/instance"
database_path="${instance_dir}/dough-log.sqlite3"
upload_dir="${instance_dir}/uploads"
backup_dir="${project_dir}/backups"
timestamp="$(date +%Y%m%d-%H%M%S)"
working_dir="$(mktemp -d)"
trap 'rm -rf "${working_dir}"' EXIT

mkdir -p "${backup_dir}"

if [[ ! -f "${database_path}" ]]; then
    echo "Database not found at ${database_path}" >&2
    exit 1
fi

sqlite3 "${database_path}" ".backup '${working_dir}/dough-log.sqlite3'"
if [[ -d "${upload_dir}" ]]; then
    cp -a "${upload_dir}" "${working_dir}/uploads"
fi

tar -C "${working_dir}" -czf "${backup_dir}/dough-log-${timestamp}.tar.gz" .
find "${backup_dir}" -maxdepth 1 -type f -name 'dough-log-*.tar.gz' -mtime +30 -delete
echo "Created ${backup_dir}/dough-log-${timestamp}.tar.gz"

