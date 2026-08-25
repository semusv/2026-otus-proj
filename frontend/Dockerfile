# Этап 8: SPA (Vite+React+TS) -> статика в nginx, наружу через Traefik на Host(localhost).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run typecheck && npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=5s \
  CMD wget -q --spider http://127.0.0.1/ || exit 1
