"""Build identity projected into operational telemetry resources."""

import re


def telemetry_identity(revision: str, installation: str) -> dict[str, str]:
    # Explicit empty values override ambient OTEL_RESOURCE_ATTRIBUTES. The
    # reference Collector drops empty or invalid identity values before storage.
    return {
        "testlookup.build.revision": revision if re.fullmatch(r"[0-9a-f]{40}", revision) else "",
        "testlookup.installation": installation if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", installation) else "",
    }
