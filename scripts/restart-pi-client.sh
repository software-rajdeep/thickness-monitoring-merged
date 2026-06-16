#!/bin/bash
# Restart the sensor client on this Ubuntu PC
echo "linux" | sudo -S systemctl restart pi-merged-client
echo "Status:"
systemctl status pi-merged-client --no-pager -l | head -20
