"""Public SDK surface for Mate module authors.

A module author writes:

    from mate.sdk import Module, ModuleContext, on_event, route, job

    class MyModule(Module):
        id = "my_module"

        @route.get("/things")
        async def list_things(self, ctx: ModuleContext): ...

        @on_event("log.imported")
        async def on_log(self, ctx: ModuleContext, payload): ...

        @route.post("/heavy")
        @job(progress=True, title="Heavy compute")
        async def heavy(self, ctx: ModuleContext): ...

The decorators only attach metadata; the platform's module loader (in
``mate.api.modules``) reads it at startup and binds the right
machinery - there is no SDK-side runtime.
"""

from mate.sdk.artifacts import (
    ARTIFACT_TYPES,
    ArtifactRef,
    ArtifactType,
    check_contract,
    get_artifact_type,
    validate_artifact,
)
from mate.sdk.context import (
    CancellationProtocol,
    EventBusProtocol,
    EventLogAccessProtocol,
    InputsProtocol,
    ModuleConfigProtocol,
    ModuleContext,
    ModuleRegistryProtocol,
    OpenEventLogProtocol,
    ProgressReporterProtocol,
    ResultCacheProtocol,
)
from mate.sdk.decorators import job, on_event, route
from mate.sdk.errors import Cancelled, ModuleError, ModuleManifestError, ReadOnlyContextError
from mate.sdk.layout import LayoutItem, ModuleLayout
from mate.sdk.manifest import (
    DependenciesPython,
    EventLogRequirements,
    InputPort,
    Link,
    Manifest,
    ManifestFrontend,
    ModuleCategory,
    OptionalModuleDep,
    Requirements,
    RuntimeJvm,
    RuntimePython,
    Source,
    WidgetDrill,
    WidgetEntry,
    WidgetHelp,
    WidgetKpi,
    WidgetView,
)
from mate.sdk.module import Module

__version__ = "0.2.0"

__all__ = [
    "ARTIFACT_TYPES",
    "ArtifactRef",
    "ArtifactType",
    "CancellationProtocol",
    "Cancelled",
    "DependenciesPython",
    "EventBusProtocol",
    "EventLogAccessProtocol",
    "EventLogRequirements",
    "InputPort",
    "InputsProtocol",
    "LayoutItem",
    "Link",
    "Manifest",
    "ManifestFrontend",
    "Module",
    "ModuleCategory",
    "ModuleConfigProtocol",
    "ModuleContext",
    "ModuleError",
    "ModuleLayout",
    "ModuleManifestError",
    "ModuleRegistryProtocol",
    "OpenEventLogProtocol",
    "OptionalModuleDep",
    "ProgressReporterProtocol",
    "ReadOnlyContextError",
    "Requirements",
    "ResultCacheProtocol",
    "RuntimeJvm",
    "RuntimePython",
    "Source",
    "WidgetDrill",
    "WidgetEntry",
    "WidgetHelp",
    "WidgetKpi",
    "WidgetView",
    "check_contract",
    "get_artifact_type",
    "job",
    "on_event",
    "route",
    "validate_artifact",
]
