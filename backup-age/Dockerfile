# Pinned tooling for encrypted volume backups (S-10.2).
#
# age is pinned rather than installed on each developer's host: a backup format
# is the last place you want a version that varies per machine, and a restore
# has to work on a box that is not the one that took the backup.
FROM alpine:3.20
RUN apk add --no-cache age=1.2.1-r0 tar
