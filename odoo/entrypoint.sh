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
        # Remove existing db_* entries first to avoid duplicates
        sed -i '/^db_host =/d; /^db_user =/d; /^db_password =/d; /^db_port =/d; /^db_name =/d' /etc/odoo/odoo.conf
        # Append database config
        echo "" >> /etc/odoo/odoo.conf
        echo "# Database connection (set by entrypoint)" >> /etc/odoo/odoo.conf
        echo "db_host = $DB_HOST" >> /etc/odoo/odoo.conf
        echo "db_user = $DB_USER" >> /etc/odoo/odoo.conf
        echo "db_password = $DB_PASS" >> /etc/odoo/odoo.conf
        echo "db_port = $DB_PORT" >> /etc/odoo/odoo.conf
        echo "db_name = $DB_NAME" >> /etc/odoo/odoo.conf
    fi
    
    # Copy update-admin script if it exists
    if [ -f /entrypoint.sh.update-admin.sh ]; then
        cp /entrypoint.sh.update-admin.sh /tmp/update-admin.sh
        chmod +x /tmp/update-admin.sh
        chown odoo:odoo /tmp/update-admin.sh
    fi
    
    # Switch to odoo user and exec the command
    # Start admin password update in background if password is set
    if [ -f /tmp/update-admin.sh ] && [ -n "$ODOO_ADMIN_PASSWORD" ] && [ "$ODOO_ADMIN_PASSWORD" != "admin" ]; then
        echo "Admin password update will run in background after Odoo starts..."
        gosu odoo /tmp/update-admin.sh &
    fi
    
    exec gosu odoo "$@"
else
    # Already running as odoo user, just ensure directory exists
    mkdir -p /var/lib/odoo/sessions
    exec "$@"
fi

