#!/bin/bash
echo "Starting debug script..." > /Users/dipakthapa/codehome/repos/nidanhis/nidan-docker/debug_log.txt
docker exec nidan-openmrs-backend ls -R /openmrs/distribution >> /Users/dipakthapa/codehome/repos/nidanhis/nidan-docker/debug_log.txt 2>&1
echo "Done." >> /Users/dipakthapa/codehome/repos/nidanhis/nidan-docker/debug_log.txt
