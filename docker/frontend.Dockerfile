# Vite dev server for the local Compose overlay. Not an OKD image.
# Node 26.13.0, the same pin as the Node stage in docker/api.Dockerfile and .nvmrc.

FROM node:26.13.0-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund \
    && chown -R node:node /frontend/node_modules

USER node

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
