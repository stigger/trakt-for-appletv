FROM alpine:3.18 AS tvbase
RUN apk add --no-cache ca-certificates py3-aiohttp py3-cryptography py3-multidict py3-yarl py3-lxml py3-paho-mqtt \
        py3-yaml py3-tz py3-requests py3-dateutil py3-mutagen py3-protobuf py3-ifaddr py3-mediafile \
        py3-zeroconf py3-netifaces

FROM tvbase AS builder
COPY . /opt/TVRemote
RUN apk add --no-cache python3-dev build-base py3-wheel py3-pip; \
    pip3 install -r /opt/TVRemote/requirements.txt

FROM tvbase AS prod
COPY --from=builder /usr/lib/python3.10/site-packages /usr/lib/python3.10/site-packages
# Create a group and user
RUN addgroup -S tvgrp && adduser -D -S -h /opt/TVRemote -s /sbin/nologin tvremote -G tvgrp
# Tell docker that all future commands should run as the tvremote user
USER tvremote
COPY . /opt/TVRemote
WORKDIR /opt/TVRemote
VOLUME /opt/TVRemote/data
#ENTRYPOINT ["/bin/sh", "/opt/TVRemote/entrypoint.sh"]
CMD ["python3", "main.py"]
