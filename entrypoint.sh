#!/bin/sh

chown -R tvremote /opt/TVRemote/data
exec sudo -u tvremote "$@"