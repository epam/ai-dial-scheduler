# DevOps runbook — Scheduler app with two DIAL-to-DIAL external services

DevOps creates the IdP clients → hands values to the DIAL admin, who sets `auth_settings`.

| External service | IdP | Target |
| --- | --- | --- |
| `external-service-1` | Azure Entra ID | DIAL deployment A |
| `external-service-2` | Keycloak | DIAL deployment B |

Shipped flow only: static pre-registered client, authorization-code grant (no PKCE/DCR). The Scheduler runs
while the user is away, so the credential must end up with a **refresh token that outlives the session** —
hence `offline_access` everywhere below.

---

## 1. Collect before you start

The token must be accepted by the **target**, per one entry of its `identityProviders` settings. Get:

- `issuerPattern`, `audience` (often unset), `rolePath` — **of the entry your token will match**
- the target's client id / app registration (usually = `audience`)
- the **callback URL** the UI sends for external-service sign-in, e.g.
  `https://admin.…/external-service-signin`. Redirect URIs are per-client, so each new client must register
  this value itself; an existing client's list gives you nothing.

  Core also has to accept it at code-exchange time: it must appear in
  `aidial.toolsets.security.allowedRedirectUris` (key sits under `toolsets` even for external services) **or**
  in the service's own `auth_settings.redirect_uri` (§5). Those lists often hold only `…/toolset-signin`, in
  which case the per-service field is what makes sign-in work.

Deployments configure several providers, matched by `issuerPattern` alone. Real dev values:

```yaml
identityProviders.azure.issuerPattern:  '^https:\/\/login\.microsoftonline\.com.+$'  # v2
identityProviders.azure.audience:       "00001111-aaaa-2222-bbbb-3333cccc4444"
identityProviders.azure.rolePath:       "roles"
identityProviders.azure1.issuerPattern: '^https:\/\/sts\.windows\.net.+$'            # v1
identityProviders.azure1.audience:      "55556666-dddd-7777-eeee-8888ffff9999"  # different audience AND rolePath
identityProviders.azure1.rolePath:      "groups"
identityProviders.keycloak.rolePath:    "dial_roles"   # custom claim (§3.4); no audience set
```

You create **one new client per external service**. Targets already have their own registration/client —
never add scopes to or change token settings on them.

---

## 2. Azure client — `external-service-1`

1. **App registrations → New registration**: `dial-scheduler-<env>` (the tenant is shared across
   environments, so the suffix keeps registrations distinguishable), single tenant,
   Redirect URI = the callback (§1), platform **Web** — not SPA, which would force PKCE and reject the
   client secret. The callback belongs on **this** registration only; the target's (§2.3) never needs it.
   Record client id + tenant id.
2. **Certificates & secrets → New client secret** → copy the **Value** (shown once). Diarise expiry —
   lapse breaks refresh for all users at once.
3. **Find the target's existing scope**: open the registration whose client id = `audience` (§1) →
   **Expose an API** → note the **Application ID URI** (`api://<id>`) and the scope name
   (`access_as_user`, or `user_impersonation` on v1-era registrations).
   - *Several scopes?* Don't guess — ask which one the target validates; admin/service scopes usually
     aren't it. Prefer the explicit scope over `.default` (which can't combine with other delegated scopes,
     AADSTS70011).
   - While there, note its **`requestedAccessTokenVersion`** (`1`/`null` → `sts.windows.net` issuer,
     `2` → `.../v2.0`): it decides which provider entry from §1 applies. Don't change it — shared.
4. **API permissions → Add a permission → APIs my organization uses** → that registration →
   **Delegated permissions** → tick the scope → Add.
5. **Consent** — admin clicks *Grant admin consent*, **or** (no admin needed) the target registration's
   owner does **Expose an API → Authorized client applications → Add a client application** → your client
   id → tick the scope. Otherwise sign-in fails AADSTS65001.

---

## 3. Keycloak client — `external-service-2`

1. **Clients → Create client**: `dial-scheduler`, *Client authentication* ON,
   *Standard flow* ON, *Direct access grants* OFF, Valid redirect URIs = the callback (§1) — on **this**
   client only; the target's client (§3.3) never needs it. Copy the secret from **Credentials**.
2. **Client scopes** → ensure **`offline_access`** is assigned (default). This is what makes the refresh
   token an offline token.
3. **Audience** — only if the target sets `audience` (often it doesn't). KC tokens default to `aud: account`:
   - Client scopes → Create client scope `aud-dial-target-b`, *Include in token scope* OFF
   - Mappers → Configure a new mapper → **Audience** → *Included Client Audience* = target's client id,
     *Add to access token* ON
   - Add that scope to `dial-scheduler` as **Default**
4. **Roles** — match the target's `rolePath`:

   | `rolePath` | Action |
   | --- | --- |
   | `realm_access.roles` | none (default) |
   | `resource_access.<client>.roles` | add a **Client roles** mapper, Token Claim Name = that path, *Add to access token* ON |
   | custom, e.g. `dial_roles` | find the client scope whose mapper emits that claim for the target's own client and assign the **same** scope here; confirm the test user has the underlying roles/groups |

5. **Endpoints** — from discovery (KC path is `/protocol/openid-connect/auth`, not `/authorize`):

   ```bash
   curl -s https://<kc-host>/realms/<realm>/.well-known/openid-configuration \
     | jq '{authorization_endpoint, token_endpoint}'
   ```

---

## 4. Handoff

### 4.1 Values (secrets over a secret channel)

| Field | `external-service-1` | `external-service-2` |
| --- | --- | --- |
| `client_id` / `client_secret` | new registration (§2.1–2.2) | new client (§3.1) |
| `authorization_endpoint` | `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/authorize` | from discovery |
| `token_endpoint` | `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token` | from discovery |
| `scopes_supported` | `["api://<target-A-id>/<scope>", "offline_access"]` — scope from the **target** (§2.3) | `["openid", "offline_access"]` |

### 4.2 `offline_access`

Core does not send `scope` on the token call — the **frontend** builds the authorize URL from
`scopes_supported`. So listing it is necessary but not sufficient; the UI must actually send it. Confirm a
refresh token was stored after the first test sign-in: without one the Scheduler fails at the first run
after the access token expires.

---

## 5. Admin: the resulting external-service config

In the dynamic config (one of `aidial.config.files`):

```jsonc
{
  "applications": {
    "scheduler": {
      "endpoint": "<scheduler-endpoint>",
      "displayName": "Scheduler",
      "external_services": {
        "external-service-1": {
          "display_name": "DIAL Deployment A",
          "auth_settings": {
            "authentication_type": "OAUTH",
            "client_id": "<§2.1 client id>",
            "client_secret": "<§2.2 secret value>",
            "authorization_endpoint": "https://login.microsoftonline.com/<tenant>/oauth2/v2.0/authorize",
            "token_endpoint": "https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token",
            "redirect_uri": "https://<admin-host>/external-service-signin",
            "scopes_supported": ["api://<target-A-id>/<scope>", "offline_access"]
          }
        },
        "external-service-2": {
          "display_name": "DIAL Deployment B",
          "auth_settings": {
            "authentication_type": "OAUTH",
            "client_id": "dial-scheduler",
            "client_secret": "<§3.1 secret>",
            "authorization_endpoint": "https://<kc-host>/realms/<realm>/protocol/openid-connect/auth",
            "token_endpoint": "https://<kc-host>/realms/<realm>/protocol/openid-connect/token",
            "redirect_uri": "https://<admin-host>/external-service-signin",
            "scopes_supported": ["openid", "offline_access"]
          }
        }
      },
      "app_identity": "<sha256 of the Scheduler's DIAL key, or its token's azp>"
    }
  }
}
```
