# Examples

## movies-demo

A project produced by `argus-test-creator demo examples/movies-demo` (the scripted fake-target
flow): `argus.yaml`, `tests/DEMO-001.yaml`, the accepted image asset and the replay frame for
Argus's fake device. Run it with Argus alone:

```bash
argus run --config examples/movies-demo/argus.yaml --test DEMO-001
```

Open it in the Creator to inspect provenance (`.argus-creator/documents/DEMO-001.json`):

```bash
argus-test-creator gui examples/movies-demo --test DEMO-001
```
