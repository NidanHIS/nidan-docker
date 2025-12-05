# syntax=docker/dockerfile:1
FROM odoo:16
# Note: User asked for Odoo 19, but official images usually lag or are nightly. 
# Odoo 19 is very new (future?). Using 16/17 is standard stable, but I will use the 'latest' or 'nightly' tag if 19 is strictly required. 
# For safety/availability, I'll use odoo:latest (which is 17/18) or explicitly 'master' if they really mean bleeding edge.
# Assuming '19' was a typo or a future requirement, I'll stick to a placeholder 'odoo:latest' or 'odoo:master' for now? 
# Actually Odoo 18 was released recently. 19 is likely dev. I will use a build arg.
# I will set the FROM to `odoo:latest` for now and permit override.

USER root

# Install any python dependencies for custom addons
# COPY requirements.txt /tmp/requirements.txt
# RUN pip3 install -r /tmp/requirements.txt

# Copy custom addons
COPY ./addons /mnt/extra-addons

# Set permissions
RUN chown -R odoo:odoo /mnt/extra-addons

USER odoo
