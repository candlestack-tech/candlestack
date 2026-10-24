# syntax=docker/dockerfile:1

# CandleStack frontend: the app, built here with Node and served by Caddy. The same image runs in
# every environment; the edge Caddy routes /api/* to the backend and everything else here. Build
# context: frontend/.
#   docker build -t candlestack-frontend --build-arg APP_COMMIT=$(git rev-parse HEAD) frontend

# The app's static build. Node on the release line the CI job `frontend` uses, pinned by digest:
# this stage only builds, nothing of it reaches the image that runs.
FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci
COPY . .
RUN npm run build

FROM caddy:2-alpine

# The caddy image is rebuilt rarely: take the Alpine fixes published since.
RUN apk upgrade --no-cache

ARG APP_COMMIT=unknown
ENV APP_COMMIT=$APP_COMMIT \
    APP_ENV=local \
    APP_VERSION=dev \
    XDG_CONFIG_HOME=/tmp/config \
    XDG_DATA_HOME=/tmp/data

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv/app

# The upstream binary carries cap_net_bind_service, which cannot be executed once the
# container drops all capabilities. Port 8080 needs none, so copy it without the file caps.
RUN cp /usr/bin/caddy /usr/bin/caddy.nocap && mv /usr/bin/caddy.nocap /usr/bin/caddy

USER 65534:65534
EXPOSE 8080
