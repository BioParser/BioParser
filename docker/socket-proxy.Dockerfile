# check=error=true
#
# The Docker API proxy for Alloy, built from source.
#
# Holds the Docker socket
# Upstream is wollomatic/socket-proxy (MIT):
#
# Go standard library only, about 150 lines on the request path
# (cmd/socket-proxy/handlehttprequest.go).

FROM golang:1.27.2-alpine3.24@sha256:f92b6ef800e499660581efdabdf25d9d817a9d124eaf900924f0504e7e27e12d AS build

ARG SOCKET_PROXY_COMMIT=bc6606ade30b0cc071cda0a1eeb5b4c518a3246b
ARG SOCKET_PROXY_VERSION=1.13.1

ADD https://github.com/wollomatic/socket-proxy.git#${SOCKET_PROXY_COMMIT} /src
WORKDIR /src
# if upstream adds a third-party dependency, the build fails here
ENV CGO_ENABLED=0 GOPROXY=off
# Same flags as upstream's own Dockerfile
RUN go build -trimpath -tags=netgo -gcflags=all=-d=checkptr \
      -ldflags="-w -s -X main.version=${SOCKET_PROXY_VERSION}" \
      -o /socket-proxy ./cmd/socket-proxy

FROM scratch
COPY --from=build /socket-proxy /socket-proxy
USER 65534:65534
ENTRYPOINT ["/socket-proxy"]
