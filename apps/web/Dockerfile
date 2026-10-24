ARG BASE_IMAGE=node:24-alpine
FROM ${BASE_IMAGE} AS development

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]

FROM development AS build
RUN npm run build

FROM ${BASE_IMAGE} AS runtime

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=5173

WORKDIR /app
COPY --from=build --chown=node:node /app/dist ./dist
COPY --chown=node:node server.mjs ./server.mjs

USER node
EXPOSE 5173
CMD ["node", "server.mjs"]
