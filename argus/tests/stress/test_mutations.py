"""Mutation backends, schema discovery, mutation types, data strategies, safety, scheduling."""

from __future__ import annotations

from typing import Any

import pytest
from tests.stress.conftest import PRODUCT_SCHEMA, make_context

from argus.adapters.fake import FakeBackend
from argus.stress.config import (
    BackendMutationsConfig,
    EntityConfig,
    SafetyConfig,
    ScheduledMutation,
    StressConfig,
)
from argus.stress.models import EntityRef, Mutation, MutationTiming
from argus.stress.mutations.backend import (
    BackendSchema,
    FieldSchema,
    RestMutationBackend,
    StateMutationBackend,
)
from argus.stress.mutations.data import DataMutationRegistry, DataMutationStrategy, generate_value
from argus.stress.mutations.scheduler import MutationExecutor, MutationScheduler
from argus.stress.mutations.types import MutationRegistry, MutationType, apply_mutation
from argus.stress.rng import DeterministicRNG
from argus.stress.safety import SafetyPolicy

# -- schema -------------------------------------------------------------------------------------


def test_schema_from_contract_and_config_merge():
    contract = {
        "environment": "test",
        "entities": {
            "users": {"operations": ["create", "update", "delete"],
                      "fields": {"name": "string", "email": "email",
                                 "status": ["active", "disabled"]}},
            "movies": {"operations": ["create", "update"],
                       "fields": {"title": "string", "rating": {"type": "number", "max": 10}}},
        },
    }
    schema = BackendSchema.from_contract(contract)
    users = schema.entities["users"]
    assert users.fields["status"].type == "enum" and users.fields["status"].values == ("active", "disabled")  # noqa: E501
    assert users.supports("delete") and users.supports("disable")  # enum has 'disabled'
    assert users.status_update("disable") == {"status": "disabled"}
    movies = schema.entities["movies"]
    assert not movies.supports("delete") and movies.supports("duplicate")
    assert movies.display_field == "title" and movies.fields["rating"].max == 10
    merged = schema.with_config({"movies": EntityConfig(operations=["create", "update", "delete"],
                                                        current_key="movieId"),
                                 "orders": EntityConfig(fields={})})
    assert merged.entities["movies"].supports("delete")
    assert merged.entities["movies"].current_key == "movieId"
    assert "orders" in merged.entities and merged.environment == "test"


def test_state_backend_discovers_collections_and_mutates():
    backend = FakeBackend({"movies": [{"id": 1, "title": "A", "rating": 5},
                                      {"id": 2, "title": "B", "rating": 7}],
                           "settings": {"theme": "dark"}, "environment": "test"})
    mb = StateMutationBackend(backend)
    schema = mb.schema()
    assert set(schema.entities) == {"movies"} and schema.environment == "test"
    assert schema.entities["movies"].fields["rating"].type == "integer"
    assert mb.list_entities("movies")[1]["title"] == "B"
    new_id = mb.create("movies", {"title": "C", "rating": 1})
    assert new_id == "3" and len(mb.list_entities("movies")) == 3
    mb.update("movies", "1", {"title": "A2"})
    assert mb.get_entity("movies", "1")["title"] == "A2"
    mb.delete("movies", "2")
    assert [m["id"] for m in mb.list_entities("movies")] == [1, 3]
    with pytest.raises(Exception, match="not found"):
        mb.delete("movies", "99")
    assert mb.identifier.startswith("state:")


def test_state_backend_preserves_mapping_shape_and_configured_entities():
    backend = FakeBackend({"catalog": {"x": {"id": "x", "name": "X"}}})
    mb = StateMutationBackend(backend, {"items": EntityConfig(state_key="catalog",
                                                              fields={"name": {"type": "string"}})})
    assert "items" in mb.schema().entities
    mb.update("items", "x", {"name": "Y"})
    assert backend.state["catalog"]["x"]["name"] == "Y"
    assert isinstance(backend.state["catalog"], dict)


class _RestStub:
    """Minimal BackendAdapter stand-in for the REST mutation backend."""

    def __init__(self) -> None:
        self.items = {"1": {"id": "1", "title": "A"}}
        self.calls: list[tuple[str, str]] = []

    class _Resp:
        def __init__(self, status: int, body: Any = None) -> None:
            self.status_code = status
            self.is_success = 200 <= status < 300
            self._body = body
            self.text = str(body)

        def json(self):
            return self._body

    def get(self, endpoint: str, **kw):
        self.calls.append(("GET", endpoint))
        if endpoint == "/schema":
            return self._Resp(200, {"environment": "test", "entities": {
                "movies": {"operations": ["create", "update", "delete"],
                           "fields": {"title": "string"}}}})
        if endpoint == "/movies":
            return self._Resp(200, {"items": list(self.items.values())})
        key = endpoint.rsplit("/", 1)[-1]
        return self._Resp(200, self.items[key]) if key in self.items else self._Resp(404, {})

    def post(self, endpoint: str, json: Any = None, **kw):
        self.calls.append(("POST", endpoint))
        new_id = str(len(self.items) + 1)
        self.items[new_id] = {"id": new_id, **json}
        return self._Resp(201, self.items[new_id])

    def patch(self, endpoint: str, json: Any = None, **kw):
        self.calls.append(("PATCH", endpoint))
        key = endpoint.rsplit("/", 1)[-1]
        if key not in self.items:
            return self._Resp(404, {})
        self.items[key].update(json)
        return self._Resp(200, self.items[key])

    def delete(self, endpoint: str, **kw):
        self.calls.append(("DELETE", endpoint))
        key = endpoint.rsplit("/", 1)[-1]
        return self._Resp(204 if self.items.pop(key, None) else 404, {})


def test_rest_backend_discovers_schema_and_uses_paths():
    stub = _RestStub()
    mb = RestMutationBackend(stub, schema_endpoint="/schema")  # type: ignore[arg-type]
    schema = mb.schema()
    assert "movies" in schema.entities and schema.environment == "test"
    assert mb.list_entities("movies")[0]["title"] == "A"
    assert mb.create("movies", {"title": "B"}) == "2"
    mb.update("movies", "1", {"title": "A1"})
    assert mb.get_entity("movies", "1")["title"] == "A1"
    assert mb.get_entity("movies", "zzz") is None
    mb.delete("movies", "2")
    assert ("DELETE", "/movies/2") in stub.calls
    with pytest.raises(Exception, match="returned 404"):
        mb.delete("movies", "2")


# -- data strategies ---------------------------------------------------------------------------


def test_data_strategies_declare_supported_types():
    registry = DataMutationRegistry()
    for name in ("null", "empty", "missing", "duplicate", "very_long_string",
                 "special_characters", "unicode", "zero", "negative", "minimum", "maximum",
                 "out_of_range", "invalid_enum", "past_date", "future_date"):
        assert registry.get(name) is not None, name
    rng = DeterministicRNG(1)
    boolean = FieldSchema(name="flag", type="boolean")
    assert {s.name for s in registry.applicable(boolean)} == {"null", "missing"}
    enum = FieldSchema(name="status", type="enum", values=("a", "b"))
    assert "invalid_enum" in {s.name for s in registry.applicable(enum)}
    assert "very_long_string" not in {s.name for s in registry.applicable(enum)}
    number = FieldSchema(name="n", type="integer", min=0, max=10)
    out = registry.get("out_of_range").apply(5, number, rng)
    assert out > 10 or out < 0
    assert registry.get("negative").apply(4, number, rng) == -4
    assert registry.get("maximum").apply(0, number, rng) == 10
    assert len(registry.get("very_long_string").apply("x", FieldSchema(name="s"), rng)) >= 256
    assert registry.get("past_date").apply(None, FieldSchema(name="d", type="date"), rng) < "2026"
    assert registry.get("future_date").apply(None, FieldSchema(name="d", type="date"), rng) > "2026"
    assert registry.applicable(enum, {"null"}) and [s.name for s in registry.applicable(enum, {"null"})] == ["null"]  # noqa: E501


def test_custom_strategy_and_value_generation():
    class Emoji(DataMutationStrategy):
        name = "emoji"
        supported_types = frozenset({"string"})

        def apply(self, value, field, rng):
            return "🙂"

    registry = DataMutationRegistry()
    registry.register(Emoji())
    assert registry.get("emoji").apply("", FieldSchema(name="s"), DeterministicRNG(1)) == "🙂"
    rng = DeterministicRNG(2)
    for field in PRODUCT_SCHEMA.entities["products"].fields.values():
        value = generate_value(field, rng)
        if field.type == "integer":
            assert 0 <= value <= 50
        if field.type == "enum":
            assert value in ("active", "disabled")
        if field.type == "email":
            assert "@" in value


# -- mutation types -------------------------------------------------------------------------------


def test_mutation_types_build_and_apply(tmp_path, fake_mutation_backend):
    registry = MutationRegistry()
    assert registry.names() == ["archive", "create", "delete", "disable", "duplicate", "update"]
    context = make_context(tmp_path, mutation_backend=fake_mutation_backend, persist=False)
    entity = PRODUCT_SCHEMA.entities["products"]
    data = DataMutationRegistry()
    target = EntityRef(entity_type="products", entity_id="1", label="Batman Begins", source="ocr")
    existing = fake_mutation_backend.get_entity("products", "1")
    kwargs = dict(data=data, enabled_strategies=set(), timing=MutationTiming.AFTER_ACTION,
                  delay=0.0)
    create = registry.get("create").build(context, entity, None, None, **kwargs)
    assert create is not None and "title" in create.parameters and "id" not in create.parameters
    outcome = apply_mutation(registry, fake_mutation_backend, create)
    assert outcome.applied and outcome.entity_id == "3"
    update = registry.get("update").build(context, entity, target, existing, **kwargs)
    assert update is not None and update.contextual and update.entity_id == "1"
    assert apply_mutation(registry, fake_mutation_backend, update).applied
    disable = registry.get("disable").build(context, entity, target, existing, **kwargs)
    assert disable is not None and disable.parameters == {"status": "disabled"} and disable.destructive  # noqa: E501
    apply_mutation(registry, fake_mutation_backend, disable)
    assert fake_mutation_backend.get_entity("products", "1")["status"] == "disabled"
    duplicate = registry.get("duplicate").build(context, entity, target, existing, **kwargs)
    assert apply_mutation(registry, fake_mutation_backend, duplicate).entity_id == "4"
    delete = registry.get("delete").build(context, entity, target, existing, **kwargs)
    assert delete is not None and delete.destructive and delete.metadata["label"] == "Batman Begins"
    assert apply_mutation(registry, fake_mutation_backend, delete).applied
    assert fake_mutation_backend.get_entity("products", "1") is None
    failed = apply_mutation(registry, fake_mutation_backend, delete)
    assert not failed.applied and failed.error_kind == "backend"
    assert registry.get("update").build(context, entity, None, None, **kwargs) is None


def test_data_strategies_are_applied_with_probability_and_serialise(tmp_path, fake_mutation_backend):  # noqa: E501
    scenario = StressConfig.model_validate({"data_mutations": {"probability": 1.0,
                                                               "max_per_mutation": 3,
                                                               "missing": True, "null": True}})
    context = make_context(tmp_path, scenario=scenario, mutation_backend=fake_mutation_backend,
                           persist=False)
    entity = PRODUCT_SCHEMA.entities["products"]
    registry = MutationRegistry()
    mutation = registry.get("create").build(
        context, entity, None, None, data=DataMutationRegistry(),
        enabled_strategies={"missing", "null"}, timing=MutationTiming.AFTER_ACTION, delay=0.0)
    assert mutation is not None and mutation.strategies
    assert Mutation.model_validate_json(mutation.model_dump_json()) == mutation
    outcome = apply_mutation(registry, fake_mutation_backend, mutation)
    assert outcome.applied
    created = fake_mutation_backend.get_entity("products", outcome.entity_id)
    for applied in mutation.strategies:
        name, field = applied.split(":")
        if name == "missing":
            assert field not in created
        if name == "null":
            assert created[field] is None


def test_custom_mutation_type_registers():
    class Corrupt(MutationType):
        name = "corrupt"

        def build(self, *a, **k):
            return None

        def apply(self, backend, mutation):
            return None

    registry = MutationRegistry()
    registry.register(Corrupt())
    assert "corrupt" in registry.names()


# -- safety --------------------------------------------------------------------------------------


def _delete(entity: str = "products") -> Mutation:
    return Mutation(mutation_type="delete", entity_type=entity, entity_id="1", destructive=True)


def _update(entity: str = "products") -> Mutation:
    return Mutation(mutation_type="update", entity_type=entity, entity_id="1",
                    parameters={"title": "x"})


def test_safety_blocks_destructive_by_default_and_unknown_environment():
    policy = SafetyPolicy(SafetyConfig())
    verdict = policy.check(_delete(), PRODUCT_SCHEMA)
    assert not verdict.allowed and verdict.code == "unsafe" and "destructive" in verdict.reason
    assert policy.check(_update(), PRODUCT_SCHEMA).allowed
    policy = SafetyPolicy(SafetyConfig(allow_destructive_mutations=True))
    verdict = policy.check(_delete(), PRODUCT_SCHEMA)  # schema has no environment
    assert verdict.code == "unknown_environment"
    policy = SafetyPolicy(SafetyConfig(allow_destructive_mutations=True, environment="prod"))
    assert policy.check(_delete(), PRODUCT_SCHEMA).code == "unsafe"
    policy = SafetyPolicy(SafetyConfig(allow_destructive_mutations=True, environment="test"))
    assert policy.check(_delete(), PRODUCT_SCHEMA).allowed
    declared = PRODUCT_SCHEMA.model_copy(update={"environment": "staging"})
    assert policy.check(_delete(), declared).code == "unknown_environment"  # contradiction


def test_safety_allowlists_denylists_capabilities_and_dry_run():
    schema = PRODUCT_SCHEMA
    policy = SafetyPolicy(SafetyConfig(allowed_entities=["users"]))
    assert "allowed_entities" in policy.check(_update(), schema).reason
    policy = SafetyPolicy(SafetyConfig(denied_entities=["products"]))
    assert "deny-listed" in policy.check(_update(), schema).reason
    policy = SafetyPolicy(SafetyConfig(denied_operations=["update"]))
    assert not policy.check(_update(), schema).allowed
    policy = SafetyPolicy(SafetyConfig(allowed_operations=["create"]))
    assert not policy.check(_update(), schema).allowed
    policy = SafetyPolicy(SafetyConfig())
    assert policy.check(_update("ghosts"), schema).code == "unsupported"
    assert policy.check(_update(), None).code == "unsupported"
    relaxed = SafetyPolicy(SafetyConfig(require_capabilities=False))
    assert relaxed.check(_update("ghosts"), None).allowed
    dry = SafetyPolicy(SafetyConfig(), dry_run=True)
    verdict = dry.check(_update(), schema)
    assert not verdict.allowed and verdict.code == "dry_run"


# -- scheduler + executor ----------------------------------------------------------------------


def _scheduler(context, backend, cfg: BackendMutationsConfig, **kw):
    registry = MutationRegistry()
    data = DataMutationRegistry()
    scheduler = MutationScheduler(cfg, backend, registry, data, enabled_strategies=set())
    safety = SafetyPolicy(kw.get("safety", SafetyConfig(allow_destructive_mutations=True,
                                                        environment="test")),
                          dry_run=context.dry_run)
    return scheduler, MutationExecutor(backend, registry, safety, scheduler)


def test_scheduler_probability_and_contextual_targeting(tmp_path, fake_mutation_backend):
    cfg = BackendMutationsConfig(enabled=True, probability=1.0, contextual_probability=1.0,
                                 operations={"delete": {"enabled": True, "weight": 1}})
    context = make_context(tmp_path, mutation_backend=fake_mutation_backend, persist=False)
    context.entity_context = [EntityRef(entity_type="products", entity_id="2",
                                        label="The Matrix", source="ocr")]
    scheduler, executor = _scheduler(context, fake_mutation_backend, cfg)
    planned = scheduler.plan(context, 1)
    assert len(planned) == 1
    mutation = planned[0].mutation
    assert mutation.mutation_type == "delete" and mutation.entity_id == "2" and mutation.contextual
    outcome = executor.execute(context, mutation)
    assert outcome.applied and context.summary.mutations == 1
    # The deleted entity is never re-targeted from stale context.
    planned = scheduler.plan(context, 2)
    assert all(p.mutation.entity_id != "2" for p in planned)
    off = BackendMutationsConfig(enabled=True, probability=0.0)
    scheduler, _ = _scheduler(context, fake_mutation_backend, off)
    assert scheduler.plan(context, 3) == []


def test_scheduled_mutation_fires_once_after_index(tmp_path, fake_mutation_backend):
    cfg = BackendMutationsConfig(enabled=True, probability=0.0, scheduled=[
        ScheduledMutation(mutation="update", entity="products", entity_id="1",
                          after_action_index=3, data={"title": "Changed"}, delay="250ms"),
    ])
    context = make_context(tmp_path, mutation_backend=fake_mutation_backend, persist=False)
    scheduler, executor = _scheduler(context, fake_mutation_backend, cfg)
    assert scheduler.plan(context, 1) == [] and scheduler.plan(context, 2) == []
    planned = scheduler.plan(context, 3)
    assert len(planned) == 1 and planned[0].mutation.parameters["title"] == "Changed"
    assert planned[0].mutation.delay == 0.25 and planned[0].mutation.metadata["scheduled"]
    executor.execute(context, planned[0].mutation)
    assert fake_mutation_backend.get_entity("products", "1")["title"] == "Changed"
    assert context.clock.monotonic() == 1000.25  # delay honoured through the fake clock
    assert scheduler.plan(context, 4) == []  # fires once


def test_executor_blocks_unsafe_and_dry_run_and_records_trace(tmp_path, fake_mutation_backend):
    cfg = BackendMutationsConfig(enabled=True)
    context = make_context(tmp_path, mutation_backend=fake_mutation_backend, persist=False,
                           dry_run=True)
    scheduler, executor = _scheduler(context, fake_mutation_backend, cfg)
    outcome = executor.execute(context, _update())
    assert outcome.blocked and outcome.reason == "dry run"
    assert fake_mutation_backend.calls == [] or all(c[0] == "list" for c in fake_mutation_backend.calls)  # noqa: E501
    assert context.summary.mutations_blocked == 1
    event = context.trace.recent(1)[0]
    assert event.event_type.value == "backend_mutation" and "BLOCKED" in event.describe()
    live = make_context(tmp_path, mutation_backend=fake_mutation_backend, persist=False)
    scheduler, executor = _scheduler(live, fake_mutation_backend, cfg,
                                     safety=SafetyConfig())
    outcome = executor.execute(live, _delete())
    assert outcome.blocked and outcome.error_kind == "unsafe"
    assert fake_mutation_backend.get_entity("products", "1") is not None


def test_executor_classifies_backend_failures(tmp_path, fake_mutation_backend):
    fake_mutation_backend.fail_operations.add("update")
    cfg = BackendMutationsConfig(enabled=True)
    context = make_context(tmp_path, mutation_backend=fake_mutation_backend, persist=False)
    _scheduler_, executor = _scheduler(context, fake_mutation_backend, cfg)
    outcome = executor.execute(context, _update())
    assert not outcome.applied and not outcome.blocked and outcome.error_kind == "backend"
