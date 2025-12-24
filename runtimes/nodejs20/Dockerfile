FROM node:20-slim

WORKDIR /action

COPY package.json .

COPY proxy.js .

EXPOSE 8080

USER node

CMD ["node", "proxy.js"]
