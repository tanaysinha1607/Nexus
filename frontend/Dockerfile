FROM node:20-alpine

WORKDIR /app

# Copy only package.json (not lock file) to ensure npm resolves
# platform-specific optional deps (rolldown native bindings) for linux-musl.
COPY package.json ./
RUN npm install

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
