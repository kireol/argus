"""CI/CD orchestration layer.

``argus ci run`` is an orchestration layer over the existing test engine
(:class:`~argus.engine.runner.TestRunner`): it detects the CI environment,
resolves suites into the engine's own selection filters, applies retry and
quality policies, organizes artifacts, and publishes provider reports. It
never re-implements test execution.

Import submodules directly (``argus.ci.runner``, ``argus.ci.context`` ...);
this package deliberately has no eager imports so that ``argus.config`` can
depend on the lightweight vocabulary in :mod:`argus.ci.categories`.
"""
