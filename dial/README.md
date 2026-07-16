# DIAL Docker Compose

1. Setup model endpoints and keys

Create  `./core/config.json` by populating ./core/config-template.json with correct values for REMOTE_DIAL_URL and REMOTE_DIAL_API_KEY

2. Run the Docker Compose file

```
docker compose -f <filename> up
```

3. Start using DIAL

|URL|Description|
|---|---|
|http://localhost:3100|DIAL Chat|
|http://localhost:8900|Keycloak|

There are two default DIAL users pre-defined in Keycloak:

|Username|Password|Description|
|---|---|---|
|user|dial|Has only basic access to DIAL Chat|
|dial|dial|Has admin access to DIAL Chat|
