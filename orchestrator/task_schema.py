from dataclasses import dataclass, field
from typing import Literal, Any

VALID_PLATFORMS = {"web", "mobile", "desktop", "desktop_windows", "cross_platform"}

@dataclass
class BenchmarkTask:
    id:           str
    task:         str
    platform:     Literal["web", "mobile", "desktop", "desktop_windows", "cross_platform"]
    benchmark:    str | None = None
    benchmark_id: str | None = None
    split:        str | None = None
    app:          str | None = None
    source_file:  str | None = None
    extra:        dict[str, Any] = field(default_factory=dict)
