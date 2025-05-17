from __future__ import annotations

from types import MappingProxyType
from typing import Annotated, Any, TypeVar, get_args, get_origin, get_type_hints

from pydantic import BaseModel, Field
from pydantic._internal._generics import PydanticGenericMetadata
from pydantic._internal._model_construction import ModelMetaclass
from sqlalchemy import Select
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql._typing import _ColumnExpressionArgument

from fa_filter.core.exceptions import FAFilterError

_OPERATORS = {
    "gt": lambda value, column: column.__gt__(value),
    "gte": lambda value, column: column.__ge__(value),
    "lt": lambda value, column: column.__lt__(value),
    "lte": lambda value, column: column.__le__(value),
    "eq": lambda value, column: column.__eq__(value),
    "neq": lambda value, column: column.__ne__(value),
    "in": lambda value, column: column.in_(value),
    "nin": lambda value, column: column.not_in(value),
    "like": lambda value, column: column.like(f"%{value}%"),
    "ilike": lambda value, column: column.ilike(f"%{value}%"),
    "exists": lambda value, column: (
        column.has(value) if bool(value) else ~column.has(value)
    ),
}

Manual = "Manual"


class reset(set):  # type: ignore
    """
    Класс для обозначения сброса родительский значений при наследовании
    """

    pass


class FilterSettings:
    """Filter settings: SQLAlchemy model and allowed sorting fields."""

    model: type[DeclarativeBase] | None = None
    allowed_orders_by: list[str] = []


class _FilterMeta(ModelMetaclass):
    def __new__(  # noqa: PLR0912
        mcs,
        cls_name: str,
        bases: tuple[type[Any], ...],
        namespace: dict[str, Any],
        __pydantic_generic_metadata__: PydanticGenericMetadata | None = None,
        __pydantic_reset_parent_namespace__: bool = True,
        _create_model_module: str | None = None,
        **kwargs: Any,
    ) -> type[Filter]:
        new_cls = super().__new__(
            mcs,
            cls_name,
            bases,
            namespace,
            __pydantic_generic_metadata__,
            __pydantic_reset_parent_namespace__,
            _create_model_module,
            **kwargs,
        )
        if cls_name == "Filter":
            return new_cls

        if not isinstance(new_cls.__manual_fields__, set):  # type: ignore
            raise ValueError(f"{cls_name}.__manual_fields__ is not a set")

        if not isinstance(new_cls.__manual_fields__, reset):  # type: ignore
            pass  # TODO Здесь нужно сделать наследование

        model = None
        if settings_cls := namespace.get("Settings"):
            model = settings_cls.model
        else:
            namespace["Settings"] = FilterSettings
        annotations = {}
        for field_name, field_type in get_type_hints(
            new_cls, include_extras=True,
        ).items():
            if field_name.startswith("__") and field_name.endswith("__"):
                continue
            if get_origin(field_type) is Annotated and Manual in get_args(field_type):
                new_cls.__manual_fields__.add(field_name)  # type: ignore
                continue
            elif not model:
                raise FAFilterError(
                    "For clarity, all filter fields without a Settings.model should be marked as manual",
                )
            annotations[field_name] = field_type
        if model:
            filter_map = {}
            for attr_name in annotations:
                try:
                    attr_name_split = attr_name.split("__")
                    if len(attr_name_split) != 2:  # noqa: PLR2004
                        raise ValueError(
                            f"Unsupported filter '{attr_name}' in {cls_name}",
                        )
                    column_name, operator_key = attr_name_split
                    if operator_key not in _OPERATORS:
                        raise ValueError(
                            f"Unsupported operator '{operator_key}' in {attr_name}",
                        )
                    column = getattr(model, column_name)
                    filter_map[attr_name] = (column, operator_key)
                except AttributeError as exc:
                    raise ValueError(
                        f"Column '{column_name}' not found in model {model.__name__}",
                    ) from exc
            new_cls.__filter_map__ = MappingProxyType(filter_map)  # type: ignore
        return new_cls


T = TypeVar("T")


class Filter(BaseModel, metaclass=_FilterMeta):
    """
    Base class for creating SQLAlchemy query filters.
    Supports filtering, sorting, limits, and offsets.

    Attributes:
        limit: Maximum number of records to return.
        offset: Number of records to skip.
        order_by: List of columns to sort by (prefix with '-' for descending order).
    """

    __filter_map__: dict[str, Any] = {}
    __manual_fields__: set[str] = (
        reset()
    )  # Возможно нужно добавть при наследовании и добавть функцию reset() которая сбрасывает __manual_fields__ при наследовании reset("limit", "offset", "order_by") например так, такой способ не берет __manual_fields__ родителя  # Возможно нужно добавть при наследовании и добавть функцию reset() которая сбрасывает __manual_fields__ при наследовании reset("limit", "offset", "order_by") например так, такой способ не берет __manual_fields__ родителя

    limit: Annotated[int | None, Manual] = Field(
        default=None,
        gt=0,
        description="Maximum number of items to return",
    )
    offset: Annotated[int | None, Manual] = Field(
        default=None,
        gt=0,
        description="Number of items to skip before starting to collect the result set",
    )
    order_by: Annotated[list[str] | None, Manual] = Field(
        default=None,
        description="List of column names to order by, prefixed with '-' for descending order. Order matters.",
    )

    class Settings(FilterSettings):
        pass

    def __init__(self, /, **data: Any) -> None:
        super().__init__(**data)
        self.__filter_list: list[_ColumnExpressionArgument[bool]] = []

    def __call__(self, stmt: Select[tuple[T]]) -> Select[tuple[T]]:
        """Applies filter, sorting, limit, and offset to the query."""

        return self.filter_(self.order_by_(self.limit_(self.offset_(stmt))))

    def filter_(self, stmt: Select[tuple[T]]) -> Select[tuple[T]]:
        """Applies filters to the query."""

        if not self.__filter_list:
            self.__parse()
        if self.__filter_list:
            return stmt.filter(*self.__filter_list)
        return stmt

    def order_by_(self, stmt: Select[tuple[T]]) -> Select[tuple[T]]:
        """Applies sorting to the query."""

        if self.order_by:
            _order_by = []
            for column_name in self.order_by:
                direction = "desc" if column_name.startswith("-") else "asc"
                column_name = column_name.lstrip("-")  # noqa: PLW2901
                if column_name not in self.Settings.allowed_orders_by:
                    continue
                column = getattr(self.Settings.model, column_name)
                _order_by.append(getattr(column, direction)())
            return stmt.order_by(*_order_by)
        return stmt

    def limit_(self, stmt: Select[tuple[T]]) -> Select[tuple[T]]:
        """Applies limit to the query."""

        if self.limit:
            return stmt.limit(self.limit)
        return stmt

    def offset_(self, stmt: Select[tuple[T]]) -> Select[tuple[T]]:
        """Applies offset to the query."""

        if self.offset:
            return stmt.offset(self.offset)
        return stmt

    def __parse(self) -> None:
        """Generates a list of filters based on model data."""

        if not self.__filter_map__:
            return
        filters_data = self.model_dump(
            exclude_unset=True,
            exclude_none=True,
            exclude=self.__manual_fields__,
        )
        for field_name, value in filters_data.items():
            column, operator_key = self.__filter_map__[field_name]
            filter_expr = _OPERATORS[operator_key](value, column)
            self.__filter_list.append(filter_expr)
