#!/bin/sh

chown -R tvremote /opt/TVRemote/data
export PYTHONUNBUFFERED=1
exec sudo -E -u tvremote "$@"