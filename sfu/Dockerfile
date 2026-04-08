FROM golang:1.24-alpine AS builder
RUN apk add --no-cache git
WORKDIR /src
COPY go.mod ./
RUN go mod download || true
COPY . .
RUN go mod tidy && CGO_ENABLED=0 go build -o /sfu .

FROM alpine:3.19
COPY --from=builder /sfu /usr/local/bin/sfu
ENTRYPOINT ["sfu"]
