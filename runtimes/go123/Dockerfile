# Stage 1: Build the proxy
FROM golang:1.23-alpine AS builder

WORKDIR /build

COPY main.go .
COPY go.mod .
COPY go.sum .

RUN go build -o proxy main.go

# Stage 2: Runtime environment
FROM alpine:latest

# Install Go compiler for action compilation
RUN apk add --no-cache go

WORKDIR /app

# Copy proxy binary
COPY --from=builder /build/proxy /proxy

EXPOSE 8080

USER nobody

CMD ["/proxy"]
