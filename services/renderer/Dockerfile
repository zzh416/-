ARG BASE_IMAGE=mcr.microsoft.com/playwright:v1.64.0-noble
FROM ${BASE_IMAGE}

ENV NODE_ENV=production

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

COPY --chown=pwuser:pwuser src ./src
COPY --chown=pwuser:pwuser test ./test

USER pwuser

EXPOSE 9100

CMD ["node", "src/server.mjs"]
