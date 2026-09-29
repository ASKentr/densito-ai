# Базовые образы зафиксированы по версии и digest (ТЗ п.3.2).
# vite 8 требует Node ^20.19 || >=22.12; 22.23.2 — версия, на которой проверялась сборка.
FROM node:22.23.2-alpine3.24@sha256:b6f26b36c8ff49624cfdac716b8ea1138d606df02586a77d364bb5536a634f85 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE=http://localhost:8000
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:1.31.6-alpine3.24@sha256:d10753d9289b8e3f884386351f73554ce72b631378949deddd75e83ee296c427
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
