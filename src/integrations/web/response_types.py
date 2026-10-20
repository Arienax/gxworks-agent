"""HTTP resource validation, with explicitly declared engineering data fields."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, TypeVar, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, model_validator
from shared.privacy import private_field

JsonObject = dict[str, JsonValue]
T = TypeVar("T")


class EngineeringData:
    """Field-owned user data; its keys do not name transport credentials."""


ENGINEERING_DATA = EngineeringData()
EngineeringValue = Annotated[JsonValue, ENGINEERING_DATA]
EngineeringObject = Annotated[JsonObject, ENGINEERING_DATA]
EngineeringMap = Annotated[dict[str, T], ENGINEERING_DATA]


def _require_public(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if private_field(key):
                raise ValueError("Private data cannot be returned as an HTTP resource")
            _require_public(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _require_public(nested)
    return value


def _owned_field(annotation, metadata=()):
    if any(isinstance(item, EngineeringData) for item in metadata):
        return True
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return True  # The nested resource validates its own fields.
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        return _owned_field(args[0], args[1:])
    return any(_owned_field(arg) for arg in get_args(annotation) if arg is not type(None))


class PublicResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def require_public(cls, value):
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="python", exclude_unset=True)
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if private_field(key):
                    raise ValueError("Private data cannot be returned as an HTTP resource")
                field = cls.model_fields.get(key)
                if field is None:
                    field = next((item for item in cls.model_fields.values()
                                  if key == item.alias or key == item.validation_alias), None)
                if field is None or not _owned_field(field.annotation, field.metadata):
                    _require_public(nested)
        return value


class ExtensibleResource(PublicResource):
    """Public metadata extensions remain checked, including nested fields."""
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)


class PublicObject(RootModel[JsonObject]):
    """Legacy metadata validator; concrete HTTP routes must use named resources."""
    @model_validator(mode="before")
    @classmethod
    def require_public(cls, value):
        return _require_public(value)


class NullablePublicObject(RootModel[JsonObject | None]):
    @model_validator(mode="before")
    @classmethod
    def require_public(cls, value):
        return _require_public(value)
