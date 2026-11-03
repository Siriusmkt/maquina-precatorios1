# ---- build ----
FROM node:22.20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
# --ignore-scripts: nenhuma dependencia roda codigo arbitrario na instalacao.
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY . .
RUN npm run build

# ---- runtime: nginx sem root, porta 8080 ----
FROM nginxinc/nginx-unprivileged:1.28-alpine
ENV API_UPSTREAM=api:8000 \
    PROXY_CONFIAVEL=10.0.0.0/8 \
    RAIZ_ESTATICA=/usr/share/nginx/html \
    NGINX_ENVSUBST_FILTER="^(API_UPSTREAM|PROXY_CONFIAVEL|RAIZ_ESTATICA)$"
COPY nginx/seguranca.conf /etc/nginx/maquina/seguranca.conf
COPY nginx/maquina.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
