ARG POLARIS_NODE_BASE_IMAGE
FROM ${POLARIS_NODE_BASE_IMAGE} AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --fund=false

FROM dependencies AS build
COPY app app
COPY build build
COPY components components
COPY db db
COPY drizzle drizzle
COPY features features
COPY lib lib
COPY public public
COPY worker worker
COPY drizzle.config.ts eslint.config.mjs next.config.ts ./
COPY postcss.config.mjs tsconfig.json vite.config.ts ./
RUN npm run build

FROM ${POLARIS_NODE_BASE_IMAGE} AS runtime
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build --chown=1000:1000 /app /app
USER 1000:1000
EXPOSE 3000
CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
