from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import declared_attr
from sqlmodel import Field, SQLModel


convention = {
    "ix": "IX_%(column_0_label)s",
    "uq": "UQ_%(table_name)s_%(column_0_name)s",
    "ck": "CK_%(table_name)s_%(constraint_name)s",
    "fk": "FK_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "PK_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)

SQLModel.metadata = metadata


class BaseSQLModel(SQLModel):
    __abstract__ = True

    @declared_attr.directive
    def __tablename__(cls) -> str:  # type: ignore[override]
        name = cls.__name__
        result = ["_" + char.lower() if char.isupper() and i > 0 else char.lower() for i, char in enumerate(name)]
        return "".join(result) + "s"

    created_at: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
        sa_column_kwargs={"server_default": func.now(), "nullable": False},
    )

    updated_at: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
        sa_column_kwargs={
            "server_default": func.now(),
            "onupdate": func.now(),
            "nullable": False,
        },
    )
