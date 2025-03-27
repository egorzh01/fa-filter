# Example SQLAlchemy model
from datetime import datetime

from pydantic import Field
from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fa_filter.sqlalchemy.filter import Filter

Base = declarative_base()


class DocumentAutosave(Base):
    __tablename__ = "document_autosaves"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=datetime.now,
    )

    document: Mapped["Document"] = relationship(
        back_populates="autosave",
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        comment="Document ID",
    )
    name: Mapped[str] = mapped_column(
        comment="Document name",
    )
    autosave: Mapped[DocumentAutosave | None] = relationship(
        back_populates="document",
    )


class DocumentsFilter(Filter):

    autosave__exists: bool | None = None
    name__like: str | None = Field(
        description="Document name pattern",  # Display in FastAPI Swagger
    )

    class Settings(Filter.Settings):
        model = Document
        allowed_orders_by = ["name"]


# Example FastAPI integration
from fastapi import Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

app = FastAPI()


async def get_db_session() -> AsyncSession: ...


@app.get(
    path="/documents/",
    response_model=list[dict],
)
async def get_documents_list(
    db_session: AsyncSession = Depends(get_db_session),
    documents_filter: DocumentsFilter = Query(),
):
    stmt = select(Document)
    stmt = documents_filter(stmt)
    # or filter_(stmt) or order_by_(stmt) or limit_(stmt) or offset_(stmt)
    documents = await db_session.scalars(stmt)
    return [{"name": doc.name} for doc in documents]
