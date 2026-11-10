# mate-sdk

Public Python SDK for Mate module authors. Re-exports `Module`, `ModuleContext`, the `@route` / `@on_event` / `@job` decorators, and the typed event/capability registry.

Also ships:

- `mate-module` (`mate.sdk.cli`): `new <id> [--panel]` scaffolds a module, `validate [paths]` checks manifest, `HOW_TO_USE.md` and `layout.json`, `test <id>` runs a module's tests.
- `mate.sdk.testing`: `build_test_context`, `sample_log` and `load_module` for module tests without the platform.
- `mate.sdk.layout`: `ModuleLayout` / `LayoutItem`, the schema of a module's declarative `layout.json` page.
