#!/bin/sh
# Block until the database schema is current, then hand over to the real command.
#
# Only the web service runs `migrate` (three services migrating at once would
# race for the same locks and could leave a half-applied schema). Render starts
# all three at the same moment, so on any deploy carrying a new migration the
# worker and beat reach for tables the web service has not created yet. Left
# alone they crash, Render restarts them, and they recover - at the cost of a
# stack trace in the log that looks exactly like a real fault.
#
# Waiting quietly says the same thing without crying wolf. `migrate --check`
# exits non-zero while migrations are outstanding, which is precisely the
# condition to wait on.
#
# The wait is bounded: a database that is genuinely unreachable must fail
# loudly, not hang in "starting" forever.
set -u

PYTHON="${PYTHON:-python}"
ATTEMPTS="${MIGRATION_WAIT_ATTEMPTS:-100}"
INTERVAL="${MIGRATION_WAIT_INTERVAL:-3}"

attempt=1
output=""
while [ "$attempt" -le "$ATTEMPTS" ]; do
    if output=$("$PYTHON" manage.py migrate --check 2>&1); then
        echo "wait_for_migrations: schema is up to date after ${attempt} check(s)."
        exit 0
    fi
    # Every attempt would be noise; silence would look like a hang.
    if [ "$attempt" -eq 1 ] || [ $((attempt % 10)) -eq 0 ]; then
        echo "wait_for_migrations: waiting for the web service to migrate (${attempt}/${ATTEMPTS})."
    fi
    attempt=$((attempt + 1))
    sleep "$INTERVAL"
done

echo "wait_for_migrations: gave up after ${ATTEMPTS} attempts. Last error:" >&2
echo "$output" >&2
exit 1
