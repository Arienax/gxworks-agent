"""Dependency-free model profiles shared by Core and Web tests."""


def offline_runtime_profile(model: str = "offline") -> dict[str, str]:
    """Return a fresh canonical profile for an injected synthetic provider.

    Runtime materialization requires adapter, baseUrl and model. The reserved
    .invalid endpoint prevents this fixture from identifying a real service;
    tests must still inject a provider rather than invoke a live transport.
    No Web, pytest, application, credential or model SDK imports belong here.
    """
    return {
        "adapter": "openai_compatible",
        "baseUrl": "https://offline.invalid/v1",
        "model": model,
    }
