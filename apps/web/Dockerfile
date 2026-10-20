ARG BASE_IMAGE=node:24-alpine
FROM ${BASE_IMAGE}

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]
