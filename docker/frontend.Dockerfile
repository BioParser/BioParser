# Vite dev server for the local Compose overlay. Not an OKD image.
# Same Node pin as .nvmrc and the ui stage in docker/api.Dockerfile.

FROM node:26.13.1-slim@sha256:e6bedef217677b276c591795e169840edd9096dbf13f29df8ef8d1856b8820bc

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund \
    && chown -R node:node /frontend/node_modules

USER node

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
