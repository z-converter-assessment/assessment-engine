FROM alpine:3.19

RUN apk add --no-cache bash curl python3 iproute2 util-linux

WORKDIR /agent
COPY push.sh .
RUN chmod +x push.sh

CMD ["bash", "push.sh"]
