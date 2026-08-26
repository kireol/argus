"""Mutation backends — the chaos side of the Argus backend adapter.

A :class:`MutationBackend` exposes *entities* with declared operations and
fields (a :class:`BackendSchema`), and performs create/update/delete on them.
Capabilities are discoverable and enforced before execution: the engine never
assumes a backend supports an operation.

Three implementations ship:

* :class:`StateMutationBackend` — entities live in collections inside the
  state document served by the existing ``BackendAdapter`` (``get_state`` /
  ``set_state``). This is how the Argus demo/fake world works.
* :class:`RestMutationBackend` — conventional REST collections
  (``GET/POST /movies``, ``PATCH/DELETE /movies/{id}``) with an optional
  schema-discovery endpoint returning the contract from the spec.
* :class:`FakeMutationBackend` — in-memory, for tests.

Explicit ``backend_mutations.entities`` configuration always wins over (and
fills gaps in) automatic discovery.
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from argus.adapters.backend import BackendAdapter
from argus.exceptions import BackendError
from argus.stress.config import EntityConfig, EntityFieldConfig

FIELD_TYPES = ("string", "number", "integer", "boolean", "enum", "date", "email", "id", "object",
               "list")


class FieldSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    type: str = "string"
    values: tuple[Any, ...] = ()
    min: float | None = None
    max: float | None = None
    required: bool = False
    display: bool = False

    @classmethod
    def from_config(cls, name: str, cfg: EntityFieldConfig) -> FieldSchema:
        return cls(name=name, type=cfg.type, values=tuple(cfg.values), min=cfg.min, max=cfg.max,
                   required=cfg.required, display=cfg.display)

    @classmethod
    def from_contract(cls, name: str, spec: Any) -> FieldSchema:
        """``"string"`` | ``["a", "b"]`` (enum) | ``{"type": ..., "min": ...}``."""
        if isinstance(spec, list):
            return cls(name=name, type="enum", values=tuple(spec))
        if isinstance(spec, dict):
            values = spec.get("values") or spec.get("enum") or ()
            type_ = str(spec.get("type", "enum" if values else "string"))
            return cls(name=name, type=type_, values=tuple(values),
                       min=spec.get("min"), max=spec.get("max"),
                       required=bool(spec.get("required", False)),
                       display=bool(spec.get("display", False)))
        return cls(name=name, type=str(spec))


class EntitySchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    operations: frozenset[str] = frozenset({"create", "update", "delete"})
    fields: dict[str, FieldSchema] = Field(default_factory=dict)
    id_field: str = "id"
    state_key: str | None = None
    path: str | None = None
    current_key: str | None = None
    disable: dict[str, Any] = Field(default_factory=dict)
    archive: dict[str, Any] = Field(default_factory=dict)

    def supports(self, operation: str) -> bool:
        if operation in ("disable", "archive"):
            return "update" in self.operations and bool(self._status_fields(operation))
        if operation == "duplicate":
            return "create" in self.operations
        return operation in self.operations

    def _status_fields(self, operation: str) -> dict[str, Any]:
        explicit = self.disable if operation == "disable" else self.archive
        if explicit:
            return dict(explicit)
        for field in self.fields.values():
            if field.type == "enum":
                for value in field.values:
                    if str(value).lower().startswith(operation[:5]):
                        return {field.name: value}
            if field.type == "boolean" and field.name in ("active", "enabled", "available") and (
                operation == "disable"
            ):
                return {field.name: False}
            if field.type == "boolean" and field.name == "archived" and operation == "archive":
                return {field.name: True}
        return {}

    def status_update(self, operation: str) -> dict[str, Any]:
        return self._status_fields(operation)

    @property
    def display_field(self) -> str | None:
        for field in self.fields.values():
            if field.display:
                return field.name
        for candidate in ("title", "name", "label", "email"):
            if candidate in self.fields:
                return candidate
        return None

    @classmethod
    def from_config(cls, name: str, cfg: EntityConfig) -> EntitySchema:
        return cls(
            name=name, operations=frozenset(cfg.operations),
            fields={k: FieldSchema.from_config(k, v) for k, v in cfg.fields.items()},
            id_field=cfg.id_field, state_key=cfg.state_key or name, path=cfg.path,
            current_key=cfg.current_key, disable=dict(cfg.disable), archive=dict(cfg.archive),
        )

    def merged_with(self, cfg: EntityConfig) -> EntitySchema:
        """Explicit configuration overrides discovered facts."""
        fields = dict(self.fields)
        for k, v in cfg.fields.items():
            fields[k] = FieldSchema.from_config(k, v)
        return self.model_copy(update={
            "operations": frozenset(cfg.operations) if cfg.operations else self.operations,
            "fields": fields, "id_field": cfg.id_field or self.id_field,
            "state_key": cfg.state_key or self.state_key, "path": cfg.path or self.path,
            "current_key": cfg.current_key or self.current_key,
            "disable": dict(cfg.disable) or self.disable,
            "archive": dict(cfg.archive) or self.archive,
        })


class BackendSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    entities: dict[str, EntitySchema] = Field(default_factory=dict)
    environment: str | None = None
    supports_concurrency: bool = True
    source: str = "configured"

    @classmethod
    def from_contract(cls, contract: dict[str, Any], *, source: str = "discovered") -> BackendSchema:  # noqa: E501
        entities: dict[str, EntitySchema] = {}
        for name, spec in (contract.get("entities") or {}).items():
            spec = spec or {}
            fields = {k: FieldSchema.from_contract(k, v) for k, v in (spec.get("fields") or {}).items()}  # noqa: E501
            entities[name] = EntitySchema(
                name=name, operations=frozenset(spec.get("operations") or ["create", "update", "delete"]),  # noqa: E501
                fields=fields, id_field=str(spec.get("id_field", "id")),
                state_key=spec.get("state_key"), path=spec.get("path"),
                current_key=spec.get("current_key"),
            )
        return cls(entities=entities, environment=contract.get("environment"),
                   supports_concurrency=bool(contract.get("supports_concurrency", True)),
                   source=source)

    def with_config(self, configured: dict[str, EntityConfig]) -> BackendSchema:
        entities = dict(self.entities)
        for name, cfg in configured.items():
            existing = entities.get(name)
            entities[name] = existing.merged_with(cfg) if existing else EntitySchema.from_config(name, cfg)  # noqa: E501
        return self.model_copy(update={"entities": entities})


@runtime_checkable
class MutationBackend(Protocol):
    """What the mutation engine needs from a backend."""

    @property
    def identifier(self) -> str: ...

    def schema(self) -> BackendSchema: ...

    def list_entities(self, entity_type: str) -> list[dict[str, Any]]: ...

    def get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None: ...

    def create(self, entity_type: str, data: dict[str, Any]) -> str: ...

    def update(self, entity_type: str, entity_id: str, data: dict[str, Any]) -> None: ...

    def delete(self, entity_type: str, entity_id: str) -> None: ...

    def close(self) -> None: ...


# -- state-document backend ----------------------------------------------------------------


class StateMutationBackend:
    """Entities stored as collections inside ``BackendAdapter.get_state()``.

    A collection is either a list of objects or a mapping ``id → object``. The
    shape is preserved on write. Unless configured, entities are discovered as
    state keys whose value is a list of objects carrying ``id``.
    """

    def __init__(self, backend: BackendAdapter, entities: dict[str, EntityConfig] | None = None,
                 *, environment: str | None = None) -> None:
        self._backend = backend
        self._configured = entities or {}
        self._environment = environment
        self._schema: BackendSchema | None = None
        self._lock = threading.Lock()

    @property
    def identifier(self) -> str:
        base = getattr(getattr(self._backend, "_config", None), "base_url", None) or "state"
        return f"state:{base}"

    def schema(self) -> BackendSchema:
        if self._schema is None:
            discovered: dict[str, EntitySchema] = {}
            state = self._state()
            for key, value in state.items():
                items = _as_items(value)
                if items and all(isinstance(i, dict) for i in items):
                    id_field = "id" if all("id" in i for i in items) else None
                    if id_field is None:
                        continue
                    fields = _infer_fields(items)
                    discovered[key] = EntitySchema(name=key, fields=fields, state_key=key,
                                                  id_field=id_field)
            env = self._environment or _string(state.get("environment"))
            self._schema = BackendSchema(entities=discovered, environment=env,
                                         source="state").with_config(self._configured)
        return self._schema

    def invalidate(self) -> None:
        self._schema = None

    def _state(self) -> dict[str, Any]:
        state = self._backend.get_state()
        return dict(state) if isinstance(state, dict) else {}

    def _collection(self, entity_type: str) -> tuple[str, Any]:
        entity = self.schema().entities.get(entity_type)
        key = entity.state_key if entity and entity.state_key else entity_type
        state = self._state()
        return key, state.get(key)

    def _id_field(self, entity_type: str) -> str:
        entity = self.schema().entities.get(entity_type)
        return entity.id_field if entity else "id"

    def list_entities(self, entity_type: str) -> list[dict[str, Any]]:
        _key, collection = self._collection(entity_type)
        return [dict(i) for i in _as_items(collection) if isinstance(i, dict)]

    def get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        id_field = self._id_field(entity_type)
        for item in self.list_entities(entity_type):
            if str(item.get(id_field)) == str(entity_id):
                return item
        return None

    def _write(self, key: str, original: Any, items: list[dict[str, Any]]) -> None:
        id_field = "id"
        if isinstance(original, dict):
            payload: Any = {str(i.get(id_field)): i for i in items}
        else:
            payload = items
        self._backend.set_state({key: payload})

    def create(self, entity_type: str, data: dict[str, Any]) -> str:
        with self._lock:
            key, original = self._collection(entity_type)
            items = [dict(i) for i in _as_items(original) if isinstance(i, dict)]
            id_field = self._id_field(entity_type)
            new = dict(data)
            if new.get(id_field) in (None, ""):
                new[id_field] = _next_id(items, id_field)
            items.append(new)
            self._write(key, original, items)
            return str(new[id_field])

    def update(self, entity_type: str, entity_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            key, original = self._collection(entity_type)
            items = [dict(i) for i in _as_items(original) if isinstance(i, dict)]
            id_field = self._id_field(entity_type)
            for item in items:
                if str(item.get(id_field)) == str(entity_id):
                    for field, value in data.items():
                        if value is _MISSING:
                            item.pop(field, None)
                        else:
                            item[field] = value
                    self._write(key, original, items)
                    return
            raise BackendError(f"{entity_type}/{entity_id} not found in backend state.")

    def delete(self, entity_type: str, entity_id: str) -> None:
        with self._lock:
            key, original = self._collection(entity_type)
            items = [dict(i) for i in _as_items(original) if isinstance(i, dict)]
            id_field = self._id_field(entity_type)
            remaining = [i for i in items if str(i.get(id_field)) != str(entity_id)]
            if len(remaining) == len(items):
                raise BackendError(f"{entity_type}/{entity_id} not found in backend state.")
            self._write(key, original, remaining)

    def close(self) -> None:
        pass


# -- REST backend ----------------------------------------------------------------------------


class RestMutationBackend:
    """Conventional REST collections over the Argus ``BackendAdapter``."""

    def __init__(self, backend: BackendAdapter, entities: dict[str, EntityConfig] | None = None,
                 *, schema_endpoint: str | None = None, environment: str | None = None) -> None:
        self._backend = backend
        self._configured = entities or {}
        self._schema_endpoint = schema_endpoint
        self._environment = environment
        self._schema: BackendSchema | None = None

    @property
    def identifier(self) -> str:
        base = getattr(getattr(self._backend, "_config", None), "base_url", None) or "rest"
        return f"rest:{base}"

    def schema(self) -> BackendSchema:
        if self._schema is None:
            discovered = BackendSchema(source="configured", environment=self._environment)
            if self._schema_endpoint:
                response = self._backend.get(self._schema_endpoint)
                if response.is_success:
                    try:
                        contract = response.json()
                    except ValueError as exc:
                        raise BackendError(
                            f"Schema endpoint {self._schema_endpoint} returned invalid JSON.",
                            remediation="Return the mutation contract described in "
                            "docs/stress-testing.md.",
                        ) from exc
                    discovered = BackendSchema.from_contract(contract)
                    if self._environment:
                        discovered = discovered.model_copy(update={"environment": self._environment})  # noqa: E501
            self._schema = discovered.with_config(self._configured)
        return self._schema

    def _path(self, entity_type: str) -> str:
        entity = self.schema().entities.get(entity_type)
        return (entity.path if entity and entity.path else f"/{entity_type}").rstrip("/")

    def _id_field(self, entity_type: str) -> str:
        entity = self.schema().entities.get(entity_type)
        return entity.id_field if entity else "id"

    def list_entities(self, entity_type: str) -> list[dict[str, Any]]:
        response = self._backend.get(self._path(entity_type))
        _check(response, f"list {entity_type}")
        body = response.json()
        items = body.get("items") if isinstance(body, dict) and "items" in body else body
        return [dict(i) for i in _as_items(items) if isinstance(i, dict)]

    def get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        response = self._backend.get(f"{self._path(entity_type)}/{entity_id}")
        if response.status_code == 404:
            return None
        _check(response, f"get {entity_type}/{entity_id}")
        body = response.json()
        return dict(body) if isinstance(body, dict) else None

    def create(self, entity_type: str, data: dict[str, Any]) -> str:
        response = self._backend.post(self._path(entity_type), json=_clean(data))
        _check(response, f"create {entity_type}")
        try:
            body = response.json()
        except ValueError:
            body = {}
        id_field = self._id_field(entity_type)
        return str(body.get(id_field, "")) if isinstance(body, dict) else ""

    def update(self, entity_type: str, entity_id: str, data: dict[str, Any]) -> None:
        response = self._backend.patch(f"{self._path(entity_type)}/{entity_id}", json=_clean(data))  # noqa: E501
        _check(response, f"update {entity_type}/{entity_id}")

    def delete(self, entity_type: str, entity_id: str) -> None:
        response = self._backend.delete(f"{self._path(entity_type)}/{entity_id}")
        _check(response, f"delete {entity_type}/{entity_id}")

    def close(self) -> None:
        pass


# -- fake ---------------------------------------------------------------------------------------


class FakeMutationBackend:
    """In-memory mutation backend for tests (records every call)."""

    def __init__(self, schema: BackendSchema | None = None,
                 collections: dict[str, list[dict[str, Any]]] | None = None,
                 *, environment: str | None = "test", supports_concurrency: bool = True,
                 fail_operations: set[str] | None = None) -> None:
        self._schema = (schema or BackendSchema()).model_copy(update={
            "environment": environment, "supports_concurrency": supports_concurrency,
        })
        self.collections: dict[str, list[dict[str, Any]]] = {
            k: [dict(i) for i in v] for k, v in (collections or {}).items()
        }
        self.calls: list[tuple[str, str, str | None, dict[str, Any] | None]] = []
        self.fail_operations = fail_operations or set()
        self.on_change: Any = None
        self._lock = threading.Lock()

    @property
    def identifier(self) -> str:
        return "fake"

    def schema(self) -> BackendSchema:
        return self._schema

    def _items(self, entity_type: str) -> list[dict[str, Any]]:
        return self.collections.setdefault(entity_type, [])

    def _id_field(self, entity_type: str) -> str:
        entity = self._schema.entities.get(entity_type)
        return entity.id_field if entity else "id"

    def list_entities(self, entity_type: str) -> list[dict[str, Any]]:
        self.calls.append(("list", entity_type, None, None))
        return [dict(i) for i in self._items(entity_type)]

    def get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        id_field = self._id_field(entity_type)
        for item in self._items(entity_type):
            if str(item.get(id_field)) == str(entity_id):
                return dict(item)
        return None

    def _fail(self, op: str) -> None:
        if op in self.fail_operations:
            raise BackendError(f"fake backend refuses {op}")

    def create(self, entity_type: str, data: dict[str, Any]) -> str:
        with self._lock:
            self._fail("create")
            items = self._items(entity_type)
            id_field = self._id_field(entity_type)
            new = copy.deepcopy(_clean(data))
            if new.get(id_field) in (None, ""):
                new[id_field] = _next_id(items, id_field)
            items.append(new)
            self.calls.append(("create", entity_type, str(new[id_field]), new))
            self._changed()
            return str(new[id_field])

    def update(self, entity_type: str, entity_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._fail("update")
            id_field = self._id_field(entity_type)
            for item in self._items(entity_type):
                if str(item.get(id_field)) == str(entity_id):
                    for field, value in data.items():
                        if value is _MISSING:
                            item.pop(field, None)
                        else:
                            item[field] = copy.deepcopy(value)
                    self.calls.append(("update", entity_type, entity_id, _clean(data)))
                    self._changed()
                    return
            raise BackendError(f"{entity_type}/{entity_id} not found")

    def delete(self, entity_type: str, entity_id: str) -> None:
        with self._lock:
            self._fail("delete")
            id_field = self._id_field(entity_type)
            items = self._items(entity_type)
            remaining = [i for i in items if str(i.get(id_field)) != str(entity_id)]
            if len(remaining) == len(items):
                raise BackendError(f"{entity_type}/{entity_id} not found")
            items[:] = remaining
            self.calls.append(("delete", entity_type, entity_id, None))
            self._changed()

    def _changed(self) -> None:
        if callable(self.on_change):
            self.on_change()

    def close(self) -> None:
        pass


# -- helpers ----------------------------------------------------------------------------------


class _Missing:
    """Sentinel: remove this field entirely (the ``missing`` data mutation)."""

    def __repr__(self) -> str:
        return "<MISSING>"


_MISSING = _Missing()
MISSING = _MISSING


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not _MISSING}


def _as_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def _next_id(items: list[dict[str, Any]], id_field: str) -> Any:
    numeric = [int(i[id_field]) for i in items if str(i.get(id_field, "")).lstrip("-").isdigit()]
    if numeric or not items:
        return (max(numeric) + 1) if numeric else 1
    return f"new-{len(items) + 1}"


def _infer_fields(items: list[dict[str, Any]]) -> dict[str, FieldSchema]:
    fields: dict[str, FieldSchema] = {}
    sample = items[0]
    for key, value in sample.items():
        if key == "id":
            fields[key] = FieldSchema(name=key, type="id")
        elif isinstance(value, bool):
            fields[key] = FieldSchema(name=key, type="boolean")
        elif isinstance(value, int):
            fields[key] = FieldSchema(name=key, type="integer")
        elif isinstance(value, float):
            fields[key] = FieldSchema(name=key, type="number")
        elif isinstance(value, str):
            distinct = {str(i.get(key)) for i in items if key in i}
            if len(items) >= 3 and len(distinct) <= max(2, len(items) // 2) and all(
                len(v) <= 16 for v in distinct
            ):
                fields[key] = FieldSchema(name=key, type="enum", values=tuple(sorted(distinct)))
            else:
                fields[key] = FieldSchema(name=key, type="string",
                                          display=key in ("title", "name", "label"))
        elif isinstance(value, list):
            fields[key] = FieldSchema(name=key, type="list")
        elif isinstance(value, dict):
            fields[key] = FieldSchema(name=key, type="object")
    return fields


def _string(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _check(response: Any, operation: str) -> None:
    if not response.is_success:
        raise BackendError(
            f"Backend {operation} returned {response.status_code}: "
            f"{getattr(response, 'text', '')[:300]}",
            remediation="Check the entity path/id_field configuration.",
        )


__all__ = [
    "MISSING", "BackendSchema", "EntitySchema", "FakeMutationBackend", "FieldSchema",
    "MutationBackend", "RestMutationBackend", "StateMutationBackend",
]
