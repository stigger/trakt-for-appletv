FROM alpine:3.16 AS builder
COPY . /opt/TVRemote
RUN apk add --no-cache py3-aiohttp py3-cryptography py3-multidict py3-yarl py3-lxml py3-paho-mqtt \
        py3-yaml py3-tz py3-requests py3-dateutil py3-mutagen py3-protobuf py3-ifaddr py3-mediafile \
        py3-zeroconf py3-pip py3-netifaces python3-dev build-base py3-wheel; \
    pip3 install -r /opt/TVRemote/requirements.txt

FROM alpine:3.16
COPY . /opt/TVRemote
RUN apk add --no-cache py3-aiohttp py3-cryptography py3-multidict py3-yarl py3-lxml py3-paho-mqtt py3-yaml py3-tz py3-requests py3-dateutil py3-mutagen py3-protobuf py3-ifaddr py3-mediafile py3-zeroconf py3-netifaces &&\
   adduser -D -S -h /opt/TVRemote -s /sbin/nologin tvremote
COPY --from=builder /usr/lib/python3.10/site-packages /usr/lib/python3.10/site-packages
# Tell docker that all future commands should run as the tvremote user
USER tvremote
WORKDIR /opt/TVRemote
VOLUME /opt/TVRemote/data
COPY entrypoint.sh /entrypoint.sh
#ENTRYPOINT ["/bin/sh", "entrypoint.sh"]
CMD ["python3", "main.py"]
