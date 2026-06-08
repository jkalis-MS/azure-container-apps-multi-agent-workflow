FROM node:22-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY tsconfig.json ./
COPY src/ ./src/
RUN npx tsc

FROM node:22-slim
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY --from=build /app/dist/ ./dist/
COPY public/ ./public/
ENV PORT=3000
EXPOSE 3000
CMD ["node", "dist/index.js"]
