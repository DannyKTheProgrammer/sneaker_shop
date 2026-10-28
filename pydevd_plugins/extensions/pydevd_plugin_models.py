"""Плагин debugpy: у ORM- и Pydantic-моделей показывает только описанные поля, без служебных атрибутов."""
from _pydevd_bundle.pydevd_extension_api import TypeResolveProvider
from pydantic import BaseModel
from sqlalchemy import inspect
from sqlalchemy.orm.base import NO_VALUE

__all__ = ["SQLAlchemyModelResolver", "PydanticModelResolver"]


class SQLAlchemyModelResolver(TypeResolveProvider):
    def can_provide(self, type_object, type_name):
        return hasattr(type_object, "__mapper__")

    def resolve(self, obj, attribute):
        return getattr(obj, attribute)

    def get_dictionary(self, obj):
        result = {}
        for attr in inspect(obj).attrs:
            # loaded_value не делает lazy-load: никаких SQL и DetachedInstanceError из отладчика
            value = attr.loaded_value
            result[attr.key] = "<not loaded>" if value is NO_VALUE else value
        return result


class PydanticModelResolver(TypeResolveProvider):
    def can_provide(self, type_object, type_name):
        return isinstance(type_object, type) and issubclass(type_object, BaseModel)

    def resolve(self, obj, attribute):
        return getattr(obj, attribute)

    def get_dictionary(self, obj):
        cls = type(obj)
        result = {name: getattr(obj, name) for name in cls.model_fields}
        for name in cls.model_computed_fields:
            try:
                result[name] = getattr(obj, name)
            except Exception as exc:
                result[name] = f"<error: {exc!r}>"
        # поля сверх схемы при extra="allow"
        result.update(obj.model_extra or {})
        return result
