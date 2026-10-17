# Ladybug Explorer, our build: upstream's source at a pinned commit, on the LadybugDB version the
# builder and the PA MCP server use (package.json here; a test checks it against the adapter's
# pin), with the routes that could change the graph or send it anywhere taken out. Built by
# deploy/home/install-explorer.sh; see docs/research/ladybugdb-spike.md.
#
# - /api/db (reopen any path, an in-memory database, or an SSHFS mount) and /api/gpt (send the
#   schema and questions to OpenAI) are deleted from the server, and openai from the
#   dependencies. In READ_ONLY mode upstream doesn't mount /api/reset or /api/import.
# - follow-current runs the server on the current build and restarts it on each new one.

ARG NODE_IMAGE=node:20-trixie-slim@sha256:abfbe12cc943141a0c9e8c0a57d710df1dadd95d35e8662cc02958b284d1f35b

FROM ${NODE_IMAGE} AS build
ARG EXPLORER_REPOSITORY=https://github.com/LadybugDB/explorer.git
ARG EXPLORER_COMMIT=cc507f0cbbeec7691044c0d65a2bf5a87b3a26c7
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN chown node:node /app
USER node
# Fetched by commit id, which pins the content.
RUN git init -q . \
 && git fetch -q --depth 1 "${EXPLORER_REPOSITORY}" "${EXPLORER_COMMIT}" \
 && git checkout -q FETCH_HEAD \
 && rm -rf .git
COPY --chown=node:node package.json package-lock.json ./
RUN sed -i \
      -e '/const gpt = require(".\/Gpt");/d' \
      -e '/const dbConfig = require(".\/DBConfig");/d' \
      -e '/router.use("\/gpt", gpt);/d' \
      -e '/router.use("\/db", dbConfig);/d' \
      src/server/API.js \
 && rm src/server/Gpt.js src/server/DBConfig.js \
 && ! grep -nE 'Gpt|DBConfig|"/gpt"|"/db"' src/server/API.js
RUN npm ci --no-audit --no-fund \
 && NODE_OPTIONS="--max-old-space-size=4096" npm run build \
 && npm prune --omit=dev --no-audit --no-fund \
 && rm -rf node_modules/@ladybugdb/core/prebuilt node_modules/@ladybugdb/core/lbug-source

FROM ${NODE_IMAGE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends libatomic1 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /home/node/app
COPY --from=build /app/package.json /app/datasets.json ./
COPY --from=build /app/node_modules node_modules
COPY --from=build /app/dist dist
COPY --from=build /app/src src
COPY --chmod=0755 follow-current /usr/local/bin/follow-current
USER node
ENV NODE_ENV=production PORT=8000 LBUG_DIR=/graph LBUG_WASM=false MODE=READ_ONLY
EXPOSE 8000
ENTRYPOINT ["follow-current", "node", "src/server/index.js"]
