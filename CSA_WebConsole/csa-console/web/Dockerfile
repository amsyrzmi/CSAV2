# Build only a context produced by scripts/check-isolated.mjs --context.
FROM node:24.14.0-bookworm-slim@sha256:d8e448a56fc63242f70026718378bd4b00f8c82e78d20eefb199224a4d8e33d8 AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY . .
ARG CSA_CONSOLE_API_ORIGIN
ARG SOURCE_REVISION
ARG SOURCE_DATE_EPOCH
ENV CSA_CONSOLE_API_ORIGIN=$CSA_CONSOLE_API_ORIGIN \
    SOURCE_REVISION=$SOURCE_REVISION
RUN printf '%s' "$SOURCE_REVISION" | grep -Eq '^[0-9a-f]{40}$' \
 && npm run build -- --webpack \
 && printf '%s' "$CSA_CONSOLE_API_ORIGIN" > .next/standalone/console-api-origin

FROM node:24.14.0-bookworm-slim@sha256:d8e448a56fc63242f70026718378bd4b00f8c82e78d20eefb199224a4d8e33d8 AS runtime
ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/scripts/release-runtime.mjs ./scripts/release-runtime.mjs
USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then(r=>{if(r.status!==200)process.exit(1)}).catch(()=>process.exit(1))"]
CMD ["node", "scripts/release-runtime.mjs"]
