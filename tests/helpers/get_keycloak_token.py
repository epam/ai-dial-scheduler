from keycloak import KeycloakOpenID


def get_token_by_creds(username: str, password: str) -> dict:
    keycloak_openid = KeycloakOpenID(
        server_url="http://localhost:8900/",
        client_id="dial-admin-console",
        realm_name="dial",
        client_secret_key="keycloak_secret",
    )

    return keycloak_openid.token(username, password)


token = get_token_by_creds("dial", "dial")
print(token["access_token"])
