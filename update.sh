#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd -- "$project_dir"

usage() {
    echo "Usage: ./update.sh [update.zip]" >&2
}

if (( $# > 1 )); then
    usage
    exit 1
fi

for command_name in unzip uv; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "Error: $command_name is required to install updates." >&2
        exit 1
    fi
done

if (( $# == 1 )); then
    archive="$1"
    if [[ "$archive" != /* ]]; then
        archive="$project_dir/$archive"
    fi
else
    shopt -s nullglob
    archives=("$project_dir"/*.zip)
    shopt -u nullglob

    if (( ${#archives[@]} == 0 )); then
        echo "Error: no .zip update was found in $project_dir" >&2
        exit 1
    fi

    if (( ${#archives[@]} > 1 )); then
        echo "Error: more than one .zip file was found:" >&2
        printf '  %s\n' "${archives[@]##*/}" >&2
        echo "Choose one explicitly, for example:" >&2
        echo "  ./update.sh ${archives[0]##*/}" >&2
        exit 1
    fi

    archive="${archives[0]}"
fi

if [[ ! -f "$archive" ]]; then
    echo "Error: update archive not found: $archive" >&2
    exit 1
fi

archive_dir="$(cd -- "$(dirname -- "$archive")" && pwd -P)"
archive_name="$(basename -- "$archive")"
archive="$archive_dir/$archive_name"

if [[ "$archive_dir" != "$project_dir" ]]; then
    echo "Error: copy the update ZIP into $project_dir before running this script." >&2
    exit 1
fi

if [[ "$archive_name" != *.zip ]]; then
    echo "Error: the update file must end in .zip" >&2
    exit 1
fi

echo "Checking $archive_name..."
unzip -tq "$archive" >/dev/null

working_dir="$(mktemp -d "$project_dir/.update-work.XXXXXX")"
trap 'rm -rf -- "$working_dir"' EXIT
unzip -Z1 "$archive" > "$working_dir/archive-entries.txt"

while IFS= read -r entry; do
    if [[ "$entry" == /* || "$entry" == ".." || "$entry" == ../* || "$entry" == */../* || "$entry" == */.. ]]; then
        echo "Error: unsafe path in update archive: $entry" >&2
        exit 1
    fi
done < "$working_dir/archive-entries.txt"

unzip -q "$archive" -d "$working_dir/extracted"

if find "$working_dir/extracted" -type l -print -quit | grep -q .; then
    echo "Error: update archives may not contain symbolic links." >&2
    exit 1
fi

if [[ -f "$working_dir/extracted/pyproject.toml" ]]; then
    source_dir="$working_dir/extracted"
elif [[ -f "$working_dir/extracted/dough-log/pyproject.toml" ]]; then
    source_dir="$working_dir/extracted/dough-log"
else
    echo "Error: this does not appear to be a Dough Log update package." >&2
    exit 1
fi

required_paths=(pyproject.toml uv.lock run.py doughlog tests)
for required_path in "${required_paths[@]}"; do
    if [[ ! -e "$source_dir/$required_path" ]]; then
        echo "Error: update package is missing $required_path" >&2
        exit 1
    fi
done

echo "Testing the update before installation..."
(
    cd -- "$source_dir"
    uv sync --frozen
    uv run --frozen pytest
)

echo "Installing application files..."
regular_files=(pyproject.toml uv.lock README.md run.py .env.example .gitignore)
for regular_file in "${regular_files[@]}"; do
    if [[ -f "$source_dir/$regular_file" ]]; then
        cp -a -- "$source_dir/$regular_file" "$project_dir/$regular_file"
    fi
done

application_dirs=(doughlog deploy scripts tests)
for application_dir in "${application_dirs[@]}"; do
    if [[ -d "$source_dir/$application_dir" ]]; then
        mkdir -p -- "$project_dir/$application_dir"
        cp -a -- "$source_dir/$application_dir/." "$project_dir/$application_dir/"
    fi
done

echo "Synchronizing Python dependencies..."
uv sync --frozen

if [[ -f "$source_dir/update.sh" ]]; then
    cp -- "$source_dir/update.sh" "$project_dir/.update.sh.new"
    chmod 755 "$project_dir/.update.sh.new"
    mv -- "$project_dir/.update.sh.new" "$project_dir/update.sh"
fi

if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1 && systemctl cat dough-log.service >/dev/null 2>&1; then
    if (( EUID == 0 )); then
        service_command=(systemctl)
    elif command -v sudo >/dev/null 2>&1; then
        service_command=(sudo systemctl)
    else
        echo "Error: sudo is required to restart dough-log.service." >&2
        exit 1
    fi

    echo "Restarting dough-log.service..."
    "${service_command[@]}" restart dough-log.service

    if command -v curl >/dev/null 2>&1; then
        healthy=false
        for _attempt in {1..10}; do
            if curl --fail --silent --show-error http://127.0.0.1:5050/health >/dev/null 2>&1; then
                healthy=true
                break
            fi
            sleep 1
        done
        if [[ "$healthy" != true ]]; then
            echo "Error: the updated service did not pass its health check." >&2
            "${service_command[@]}" --no-pager --full status dough-log.service || true
            exit 1
        fi
    fi
else
    echo "No dough-log.service installation was found. Start the app with:"
    echo "  uv run python run.py"
fi

rm -- "$archive"
echo "Update complete. Removed $archive_name."
echo "Your .env, database, uploaded photos, and backups were preserved."
