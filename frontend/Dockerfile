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
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine AS production
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]