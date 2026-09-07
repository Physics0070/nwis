# Build the SPA, then serve the static bundle with nginx.
#
# The build context is the repository root rather than ./frontend, so this Dockerfile can
# live in docker/ without referencing a path outside its own context — which some Compose
# versions reject.

FROM node:22-alpine AS build
WORKDIR /app

# In production the SPA is served from the same origin as the API through the nginx proxy
# below, so these default to empty (relative) and no cross-origin request is made at all.
ARG VITE_API_BASE_URL=""
ARG VITE_WS_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_WS_BASE_URL=$VITE_WS_BASE_URL

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1
