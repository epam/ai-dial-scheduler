from sqlalchemy.engine import URL, make_url

from src.lib.db.enums import DbAuthMode, DbDriver
from src.lib.settings import DatabaseSettings


def build_database_url(
    db_settings: DatabaseSettings,
    *,
    driver: DbDriver | None = None,
    hide_password: bool = False,
) -> str:
    if db_settings.db_url and db_settings.db_auth_mode == DbAuthMode.CONNECTION_STRING:
        if driver is None:
            return db_settings.db_url
        # Honor the driver override (e.g. DbDriver.SYNC for APScheduler) even when a
        # verbatim connection string was provided, to satisfy plan.md constraint that
        # SQLAlchemyJobStore must always receive a psycopg2 URL.
        return make_url(db_settings.db_url).set(drivername=driver.value).render_as_string(hide_password=hide_password)

    url = URL.create(
        drivername=(driver or db_settings.db_driver).value,
        username=db_settings.db_user,
        password=db_settings.db_password,
        host=db_settings.db_host,
        port=db_settings.db_port,
        database=db_settings.db_name,
    )
    return url.render_as_string(hide_password=hide_password)


def build_azure_entra_sync_url(db_settings: DatabaseSettings) -> str:
    """Build a synchronous (psycopg2) URL for Azure Entra auth without a password.

    The Entra principal is used as the username; the short-lived access token is injected
    as the password per connection by a ``do_connect`` listener, never baked into the URL.
    """
    url = URL.create(
        drivername=DbDriver.SYNC.value,
        username=db_settings.db_azure_entra_user,
        host=db_settings.db_host,
        port=db_settings.db_port,
        database=db_settings.db_name,
    )
    return url.render_as_string(hide_password=False)
