#!/bin/bash
set -e

# Fix permissions for /var/lib/odoo directory
# This ensures the odoo user can write to sessions, filestore, etc.
if [ "$(id -u)" = "0" ]; then
    # Running as root, fix permissions
    chown -R odoo:odoo /var/lib/odoo
    # Ensure sessions directory exists and is writable
    mkdir -p /var/lib/odoo/sessions
    chown -R odoo:odoo /var/lib/odoo/sessions
    chmod 700 /var/lib/odoo/sessions
    
    # Wait for database to be ready and ensure database/user exist
    if [ -n "$HOST" ] && [ -n "$USER" ] && [ -n "$PASSWORD" ]; then
        DB_NAME="${ODOO_DB_NAME:-nidan}"
        DB_USER="${USER}"
        DB_PASS="${PASSWORD}"
        DB_HOST="${HOST}"
        DB_PORT="${PORT:-5432}"
        
        echo "Waiting for database $DB_HOST:$DB_PORT to be ready..."
        # Wait for postgres to be ready using superuser
        until PGPASSWORD="${POSTGRES_PASSWORD:-odoo}" psql -h "$DB_HOST" -p "$DB_PORT" -U "${POSTGRES_USER:-odoo}" -d postgres -c '\q' 2>/dev/null; do
            echo "Database not ready, waiting..."
            sleep 2
        done
        echo "Database is ready!"
        
        # Create user and database if they don't exist (using postgres superuser)
        echo "Ensuring database $DB_NAME and user $DB_USER exist..."
        PGPASSWORD="${POSTGRES_PASSWORD:-odoo}" psql -h "$DB_HOST" -p "$DB_PORT" -U "${POSTGRES_USER:-odoo}" -d postgres <<-EOSQL || true
            DO \$\$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_user WHERE usename = '$DB_USER') THEN
                    CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';
                    RAISE NOTICE 'User $DB_USER created';
                END IF;
            END
            \$\$;
EOSQL
        
        # Create database if it doesn't exist
        PGPASSWORD="${POSTGRES_PASSWORD:-odoo}" psql -h "$DB_HOST" -p "$DB_PORT" -U "${POSTGRES_USER:-odoo}" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1 || \
        PGPASSWORD="${POSTGRES_PASSWORD:-odoo}" psql -h "$DB_HOST" -p "$DB_PORT" -U "${POSTGRES_USER:-odoo}" -d postgres <<-EOSQL
            CREATE DATABASE $DB_NAME OWNER $DB_USER;
            GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
EOSQL
        
        # Write database connection to odoo.conf (only if not already present)
        # Remove existing entries first to avoid duplicates
        sed -i '/^db_host =/d; /^db_user =/d; /^db_password =/d; /^db_port =/d; /^db_name =/d; /^db_filter =/d; /^proxy_mode =/d' /etc/odoo/odoo.conf
        # Append database config
        echo "" >> /etc/odoo/odoo.conf
        echo "# Configured by entrypoint" >> /etc/odoo/odoo.conf
        echo "db_host = $DB_HOST" >> /etc/odoo/odoo.conf
        echo "db_user = $DB_USER" >> /etc/odoo/odoo.conf
        echo "db_password = $DB_PASS" >> /etc/odoo/odoo.conf
        echo "db_port = $DB_PORT" >> /etc/odoo/odoo.conf
        echo "db_name = $DB_NAME" >> /etc/odoo/odoo.conf
        echo "db_filter = ${ODOO_DB_FILTER:-^${DB_NAME}\$}" >> /etc/odoo/odoo.conf
        echo "proxy_mode = ${ODOO_PROXY_MODE:-True}" >> /etc/odoo/odoo.conf

        # One-time install of base + all custom addons present in /mnt/extra-addons
        ADDON_SENTINEL="/var/lib/odoo/.nidan-addons-installed"
        if [ ! -f "$ADDON_SENTINEL" ]; then
            ADDONS_DIR="/mnt/extra-addons"
            CUSTOM_MODULES=""
            for d in "$ADDONS_DIR"/*/; do
                [ -f "${d}__manifest__.py" ] && CUSTOM_MODULES="${CUSTOM_MODULES}${CUSTOM_MODULES:+,}$(basename "$d")"
            done
            INIT_MODULES="base${CUSTOM_MODULES:+,$CUSTOM_MODULES}"
            echo "First run: installing base and custom addons: $INIT_MODULES"
            gosu odoo odoo -d "$DB_NAME" -i "$INIT_MODULES" --stop-after-init
            touch "$ADDON_SENTINEL"
            chown odoo:odoo "$ADDON_SENTINEL"
            echo "Addons installed. Starting Odoo..."
        fi
    fi

    # One-time admin password / web.base.url setup (sentinel persists in odoo-data volume).
    ADMIN_SENTINEL="/var/lib/odoo/.admin-initialized"
    if [ ! -f "$ADMIN_SENTINEL" ] && [ -f /entrypoint.sh.update-admin.sh ] && [ -n "$ODOO_ADMIN_PASSWORD" ] && [ "$ODOO_ADMIN_PASSWORD" != "admin" ]; then
        cp /entrypoint.sh.update-admin.sh /tmp/update-admin.sh
        chmod +x /tmp/update-admin.sh
        chown odoo:odoo /tmp/update-admin.sh
        touch "$ADMIN_SENTINEL"
        chown odoo:odoo "$ADMIN_SENTINEL"
        echo "First run: admin password will be set in background..."
        gosu odoo /tmp/update-admin.sh &
    fi
    
    exec gosu odoo "$@"
else
    # Already running as odoo user, just ensure directory exists
    mkdir -p /var/lib/odoo/sessions
    exec "$@"
fi

