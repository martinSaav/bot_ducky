#!/bin/sh
# Nightly backup of the bot's database, run by the db-backup service in
# docker-compose.yml (same Postgres image as the database, so pg_dump matches
# the server version).
#
#   sh tools/db_backup.sh          one dump a day at BACKUP_AT, forever
#   sh tools/db_backup.sh --once   a single dump now
#
# Keeps the newest BACKUP_KEEP dumps. Custom format (-Fc): restore with
#   pg_restore -U ducky -d duckydb --clean --if-exists backups/duckydb-YYYY-MM-DD.dump
#
# Not in db/: that folder is the database's init-scripts folder, and Postgres
# would run this loop while creating a fresh database.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP="${BACKUP_KEEP:-7}"
BACKUP_AT="${BACKUP_AT:-04:00}"

dump() {
    mkdir -p "$BACKUP_DIR"
    file="$BACKUP_DIR/${POSTGRES_DB}-$(date +%Y-%m-%d).dump"
    # Written aside first: a dump cut halfway never replaces a good one.
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$file.partial"
    mv "$file.partial" "$file"
    echo "backup: wrote $file"
    # Names carry the date, so sorting them sorts by age: drop all but the newest.
    ls -1 "$BACKUP_DIR/${POSTGRES_DB}"-*.dump | sort -r | tail -n +"$((BACKUP_KEEP + 1))" |
        while read -r old; do
            rm -f "$old"
            echo "backup: removed $old"
        done
}

if [ "${1:-}" = "--once" ]; then
    dump
    exit 0
fi

echo "backup: daily at $BACKUP_AT, keeping the newest $BACKUP_KEEP"
while true; do
    now=$(date +%s)
    next=$(date -d "today $BACKUP_AT" +%s)
    if [ "$next" -le "$now" ]; then
        next=$(date -d "tomorrow $BACKUP_AT" +%s)
    fi
    sleep $((next - now))
    dump || echo "backup: FAILED" >&2
done
