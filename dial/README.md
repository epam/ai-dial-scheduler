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

## Offline credentials (DIAL-native external services)

The scheduler runs schedules as their owner while nobody is present, which needs a per-user
refresh token held by DIAL core. Two pieces of local configuration back that:

- `keycloak/import/realm-config.json` declares the confidential client
  `dial-credentials-manager`. It carries a copy of `dial-chat`'s client-role mapper, so a
  redeemed token still has the `roles` claim core's admin rules read.
- The `core` service in `docker-compose.yml` sets
  `aidial.identityProviders.keycloak.offlineClient.*`, pointing at that client.

Verify with `GET http://localhost:8081/v1/user/offline-credentials` using a user token — it must
answer `"available": true` and publish a `connect` block. `"available": false` means the core
settings are missing or wrong.

**The realm is only imported into an empty database.** After changing `realm-config.json`, run
`docker compose down && rm -rf keycloak/data/h2` before starting again, or the old realm persists.

`../scripts/dial_sign_in.py` grants admin consent and connects offline credentials in one go.
