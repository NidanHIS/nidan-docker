#!/bin/bash
# Script to update Odoo admin password from environment variables
# This runs after Odoo has started

DB_NAME="${ODOO_DB_NAME:-nidan}"
ADMIN_EMAIL="${ODOO_ADMIN_EMAIL:-admin}"
ADMIN_PASSWORD="${ODOO_ADMIN_PASSWORD:-admin}"

# Wait for Odoo to be ready
sleep 10

# Update admin password using Odoo shell
odoo shell -d "$DB_NAME" <<EOF
admin_user = env['res.users'].browse(2)
if admin_user.exists():
    admin_user.write({
        'login': '${ADMIN_EMAIL}',
        'password': '${ADMIN_PASSWORD}'
    })
    env.cr.commit()
    print(f"✅ Admin user updated: login='${ADMIN_EMAIL}'")
else:
    print("⚠️  Admin user (id=2) not found")
EOF

