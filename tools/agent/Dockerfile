FROM alpine:3.19 AS builder

RUN apk add --no-cache gcc musl-dev rabbitmq-c-dev

WORKDIR /build
COPY agent.c .
RUN gcc -O2 -std=c99 -Wall -Wextra -o agent agent.c -lrabbitmq

# ── runtime ───────────────────────────────────────────────────────────────────
FROM alpine:3.19
RUN apk add --no-cache rabbitmq-c
COPY --from=builder /build/agent /usr/local/bin/agent
CMD ["agent"]
