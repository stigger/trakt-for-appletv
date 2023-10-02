FROM alpine:3.18.4 AS builder
COPY . /opt/TVRemote
RUN apk add --no-cache py3-aiohttp py3-cryptography py3-multidict py3-yarl py3-lxml py3-paho-mqtt py3-yaml py3-pip py3-netifaces python3-dev build-base; pip3 install -r /opt/TVRemote/requirements.txt

FROM alpine:3.18.4
COPY . /opt/TVRemote
RUN apk add --no-cache py3-aiohttp py3-cryptography py3-multidict py3-yarl py3-lxml py3-paho-mqtt py3-yaml py3-pip py3-netifaces sudo &&\
   adduser -D -S -h /opt/TVRemote -s /sbin/nologin tvremote
COPY --from=builder /usr/lib/python3.11/site-packages /usr/lib/python3.11/site-packages
WORKDIR /opt/TVRemote
VOLUME /opt/TVRemote/data
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/bin/sh", "entrypoint.sh"]
CMD ["python3", "tvscrobbler.py"]
