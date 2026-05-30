FROM node:20-alpine AS base
WORKDIR /app
COPY package*.json ./
# npm install works whether or not package-lock.json is committed.
# After running locally once, commit the generated package-lock.json and
# change this line to `RUN npm ci` for faster, reproducible Docker builds.
RUN npm install --prefer-offline || npm install

# Development stage
FROM base AS development
ENV NODE_ENV=development
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# Build stage — Vite handles TypeScript compilation internally via esbuild
FROM base AS builder
# Default to empty so the bundle uses same-origin relative URLs (works behind
# any ingress, e.g. testlookup.local in k8s). Deployments that need an absolute
# URL (gcp-vm, cloudrun) override this via --build-arg.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine AS production
COPY --from=builder /app/dist /usr/share/nginx/html
# nginx:alpine runs envsubst on /etc/nginx/templates/*.template at container
# start, writing the result to /etc/nginx/conf.d/. This lets the same image
# work in any deploy target by setting BACKEND_UPSTREAM (default backend:8000
# for docker-compose; set to testlookup-backend:8000 in Kubernetes).
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV BACKEND_UPSTREAM=backend:8000
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]