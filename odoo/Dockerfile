# syntax=docker/dockerfile:1
FROM odoo:19

USER root

# Install gosu and postgresql-client for database operations
RUN apt-get update && apt-get install -y gosu postgresql-client && rm -rf /var/lib/apt/lists/*

# Fix Werkzeug compatibility issue with Python 3.12
# Downgrade werkzeug to a compatible version
RUN pip3 install --no-cache-dir 'werkzeug<3.0.0' || true

# Install any python dependencies for custom addons
# COPY requirements.txt /tmp/requirements.txt
# RUN pip3 install -r /tmp/requirements.txt

# Copy custom addons (if any)
# COPY ./addons /mnt/extra-addons
RUN mkdir -p /mnt/extra-addons

# Copy configuration file
COPY odoo.conf /etc/odoo/odoo.conf

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set permissions
RUN chown -R odoo:odoo /mnt/extra-addons

# Use entrypoint to fix permissions on startup
ENTRYPOINT ["/entrypoint.sh"]
# Odoo will automatically initialize the database on first run
# Use shell form to allow variable expansion
CMD ["sh", "-c", "odoo -d ${ODOO_DB_NAME:-nidan}"]
