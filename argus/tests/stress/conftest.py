"""Fixtures for stress tests: everything runs on fakes and a FakeClock."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from argus.artifacts.manager import TestArtifacts
from argus.config.models import AppConfig
from argus.stress.clock import FakeClock
from argus.stress.config import StressConfig
from argus.stress.context import StressContext
from argus.stress.demo import DemoStoreBackend, DemoStoreDevice, DeviceTextOCRProvider
from argus.stress.mutations.backend import (
    BackendSchema,
    EntitySchema,
    FakeMutationBackend,
    FieldSchema,
    StateMutationBackend,
)
from argus.stress.rng import DeterministicRNG
from argus.stress.trace import Trace

PRODUCT_SCHEMA = BackendSchema(entities={
    "products": EntitySchema(
        name="products", operations=frozenset({"create", "update", "delete"}),
        fields={
            "id": FieldSchema(name="id", type="id"),
            "title": FieldSchema(name="title", type="string", display=True),
            "price": FieldSchema(name="price", type="number", min=0, max=100),
            "stock": FieldSchema(name="stock", type="integer", min=0, max=50),
            "status": FieldSchema(name="status", type="enum", values=("active", "disabled")),
            "released": FieldSchema(name="released", type="date"),
            "email": FieldSchema(name="email", type="email"),
        },
        current_key="current_product",
    ),
})


def demo_app_config(tmp_path: Path, *, buggy: bool = True, **stress: Any) -> AppConfig:
    """An AppConfig using the demo store device + backend, results under tmp_path."""
    data: dict[str, Any] = {
        "backend": {"type": "stress_demo"},
        "devices": {"store": {"type": "stress_demo", "platform": "fake", "buggy": buggy,
                              "crash_on_text": "<script>"}},
        "stress": {"name": "test", "device": "store",
                   "results_dir": str(tmp_path / "stress-results"), **stress},
    }
    config = AppConfig.model_validate(data)
    config.root_dir = str(tmp_path)
    return config


def make_context(
    tmp_path: Path,
    *,
    seed: int = 1,
    scenario: StressConfig | None = None,
    device: Any = None,
    backend: Any = None,
    mutation_backend: Any = None,
    ocr: Any = None,
    clock: FakeClock | None = None,
    dry_run: bool = False,
    persist: bool = True,
) -> StressContext:
    scenario = scenario or StressConfig()
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    return StressContext(
        run_id="run-test", seed=seed, config=scenario, app_config=AppConfig(),
        rng=DeterministicRNG(seed), artifacts=TestArtifacts(run_dir, save_enabled=persist),
        trace=Trace(run_dir / "trace.jsonl" if persist else None, tail=500),
        clock=clock or FakeClock(), device=device, device_name=device.name if device else None,
        backend=backend, mutation_backend=mutation_backend, ocr=ocr, dry_run=dry_run,
        cancel=threading.Event(),
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def demo_world():
    backend = DemoStoreBackend()
    device = DemoStoreDevice("store", backend=backend, buggy=True, crash_on_text="<script>")
    device.connect()
    mutation_backend = StateMutationBackend(backend, environment="test")
    return device, backend, mutation_backend, DeviceTextOCRProvider(device)


@pytest.fixture
def fake_mutation_backend() -> FakeMutationBackend:
    return FakeMutationBackend(PRODUCT_SCHEMA, {"products": [
        {"id": 1, "title": "Batman Begins", "price": 12.99, "stock": 5, "status": "active",
         "released": "2005-06-15", "email": "a@example.test"},
        {"id": 2, "title": "The Matrix", "price": 9.99, "stock": 3, "status": "active",
         "released": "1999-03-31", "email": "b@example.test"},
    ]})
