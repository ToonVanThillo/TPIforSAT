# Testing philosophy

- **Test behaviour, not implementation.** Tests are written from the spec (`docs/design.md` and the task description), through public interfaces. Don't assert on private attributes or internal call sequences.
- **Few, meaningful tests.** Each test checks one behaviour that someone could get wrong, and its name says which behaviour. Skip trivial tests (getters, constants, framework plumbing).
- **Control law tests are pure.** The algorithm has no HA dependency, so test it directly with explicit inputs and explicit time steps. Prefer checking physically meaningful outcomes (valve shuts above target, integral doesn't wind up while saturated, leak drifts the valve open) over exact floating-point traces. Where an exact value matters, derive it by hand from the formula in the test.
- **HA glue tests use the real HA test harness.** Use `pytest-homeassistant-custom-component`. Fake only what we don't own and can't run cheaply, e.g. the VTherm thermostat runtime, the way smartPI's `FakeThermostatRuntime` does. Never mock the unit under test.
- **Deterministic.** No real sleeps and no wall-clock dependence. Inject or freeze time.
- **A failing test is information.** If a test fails because the code doesn't match the spec, report it. Don't change the test to make it pass, and don't change the code from the tester's side.
- **Static checks are part of testing.** `ruff` and `mypy` should be clean for the integration code.
