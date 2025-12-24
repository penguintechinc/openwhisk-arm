# Multi-stage build for Go invoker service
FROM golang:1.23-alpine AS builder

WORKDIR /build
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /tmp/invoker ./cmd/invoker

# Runtime stage
FROM alpine:latest

RUN apk add --no-cache ca-certificates tzdata

COPY --from=builder /tmp/invoker /invoker

EXPOSE 8085

CMD ["/invoker"]
