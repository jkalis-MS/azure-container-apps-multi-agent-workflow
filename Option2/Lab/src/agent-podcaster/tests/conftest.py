"""conftest.py — Stub heavy external packages that cannot be installed in the
lightweight test environment (Python 3.14 + ARM64, no Rust compiler, no long
paths for grpcio, etc.).

These stubs are injected into sys.modules *before* any production module is
imported, so ``from langchain_openai import AzureChatOpenAI`` and the
OpenTelemetry import chain succeed without real installs.
"""

import sys
import types
from unittest.mock import MagicMock


def _ensure_stub(dotted_name: str, attrs: dict | None = None) -> types.ModuleType:
    """Insert a stub module into sys.modules if it isn't already present."""
    if dotted_name in sys.modules:
        return sys.modules[dotted_name]
    mod = types.ModuleType(dotted_name)
    if attrs:
        for k, v in attrs.items():
            setattr(mod, k, v)
    sys.modules[dotted_name] = mod
    # ensure parent packages exist
    parts = dotted_name.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            sys.modules[parent] = types.ModuleType(parent)
    return mod


# ---- copilot SDK (github-copilot-sdk) ----------------------------------------
_mock_permission_handler = MagicMock()
_mock_permission_handler.approve_all = MagicMock()

def _fake_define_tool(**kwargs):
    """Stub for @define_tool(description=...) — returns the decorated function."""
    def decorator(fn):
        fn._tool_metadata = kwargs
        return fn
    return decorator

_copilot = _ensure_stub("copilot", {
    "CopilotClient": MagicMock,
    "Tool": MagicMock,
    "define_tool": _fake_define_tool,
})

# copilot.session sub-module (SDK 0.3.0: ``from copilot.session import PermissionHandler``)
_ensure_stub("copilot.session", {"PermissionHandler": _mock_permission_handler})

# ---- opentelemetry family ----------------------------------------------------
# We need a realistic-enough stub tree so that main.py can execute its top-level
# setup code.

# -- opentelemetry.trace
_trace = _ensure_stub("opentelemetry.trace")
_mock_tracer = MagicMock()
_trace.get_tracer = MagicMock(return_value=_mock_tracer)
_trace.set_tracer_provider = MagicMock()
_trace.get_tracer_provider = MagicMock()
_trace.SpanKind = MagicMock()
_trace.StatusCode = MagicMock()
_trace.set_span_in_context = MagicMock(return_value={})

# -- opentelemetry.context
_otel_context = _ensure_stub("opentelemetry.context")
_otel_context.attach = MagicMock()
_otel_context.detach = MagicMock()

# -- opentelemetry.sdk.trace
_sdk_trace = _ensure_stub("opentelemetry.sdk.trace")

class _FakeTracerProvider:
    def __init__(self, *a, **kw): pass
    def add_span_processor(self, *a, **kw): pass

_sdk_trace.TracerProvider = _FakeTracerProvider

# -- opentelemetry.sdk.trace.export
_sdk_trace_export = _ensure_stub("opentelemetry.sdk.trace.export")
_sdk_trace_export.BatchSpanProcessor = lambda *a, **kw: MagicMock()

# -- opentelemetry.sdk._logs / _events (used for gen_ai content capture)
_sdk_logs = _ensure_stub("opentelemetry.sdk._logs")

class _FakeLoggerProvider:
    def __init__(self, *a, **kw): pass
    def add_log_record_processor(self, *a, **kw): pass

_sdk_logs.LoggerProvider = _FakeLoggerProvider
_sdk_logs_export = _ensure_stub("opentelemetry.sdk._logs.export")
_sdk_logs_export.BatchLogRecordProcessor = lambda *a, **kw: MagicMock()

_otel_logs = _ensure_stub("opentelemetry._logs")
_otel_logs.set_logger_provider = MagicMock()

_sdk_events = _ensure_stub("opentelemetry.sdk._events")

class _FakeEventLoggerProvider:
    def __init__(self, *a, **kw): pass

_sdk_events.EventLoggerProvider = _FakeEventLoggerProvider

_otel_events = _ensure_stub("opentelemetry._events")
_otel_events.set_event_logger_provider = MagicMock()

# -- opentelemetry.exporter.otlp.proto.grpc._log_exporter
_log_exporter = _ensure_stub("opentelemetry.exporter.otlp.proto.grpc._log_exporter")
_log_exporter.OTLPLogExporter = lambda *a, **kw: MagicMock()

# -- opentelemetry.sdk.resources
_sdk_resources = _ensure_stub("opentelemetry.sdk.resources")

class _FakeResource:
    @staticmethod
    def create(*a, **kw):
        return MagicMock()

_sdk_resources.Resource = _FakeResource

# -- opentelemetry.exporter.otlp.proto.grpc.trace_exporter
_ensure_stub("opentelemetry.exporter")
_ensure_stub("opentelemetry.exporter.otlp")
_ensure_stub("opentelemetry.exporter.otlp.proto")
_ensure_stub("opentelemetry.exporter.otlp.proto.grpc")
_exporter = _ensure_stub("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
_exporter.OTLPSpanExporter = lambda *a, **kw: MagicMock()

# -- opentelemetry.instrumentation.*
_ensure_stub("opentelemetry.instrumentation")
_fastapi_inst = _ensure_stub("opentelemetry.instrumentation.fastapi")
_fastapi_inst.FastAPIInstrumentor = MagicMock()
_httpx_inst = _ensure_stub("opentelemetry.instrumentation.httpx")
_httpx_inst.HTTPXClientInstrumentor = MagicMock(return_value=MagicMock())

# -- opentelemetry (root)
_ensure_stub("opentelemetry", {"trace": _trace, "context": _otel_context})

# ---- azure.storage.blob (used in audio_utils, but not exercised in tests) ----
_ensure_stub("azure")
_ensure_stub("azure.storage")
_ensure_stub("azure.storage.blob", {
    "BlobServiceClient": MagicMock,
    "ContentSettings": MagicMock,
})

# ---- json_repair (may not be installed in lightweight test env) ---------------
_json_repair = _ensure_stub("json_repair", {"repair_json": lambda s, **kw: s})

# ---- pydub / audioop (audioop removed in Python 3.13+) ----------------------
# pydub tries to import audioop which no longer exists. Stub it so that
# audio_utils can be imported without error.
_ensure_stub("audioop", {
    "ratecv": MagicMock(),
    "lin2lin": MagicMock(),
    "bias": MagicMock(),
    "max": MagicMock(return_value=0),
    "minmax": MagicMock(return_value=(0, 0)),
    "rms": MagicMock(return_value=0),
    "avgpp": MagicMock(return_value=0),
    "maxpp": MagicMock(return_value=0),
    "cross": MagicMock(return_value=0),
    "mul": MagicMock(return_value=b""),
    "tomono": MagicMock(return_value=b""),
    "tostereo": MagicMock(return_value=b""),
    "add": MagicMock(return_value=b""),
    "byteswap": MagicMock(return_value=b""),
})

# ---- pydub itself (may not be installed) ------------------------------------
_pydub = _ensure_stub("pydub", {"AudioSegment": MagicMock})
_pydub_audio = _ensure_stub("pydub.audio_segment", {"AudioSegment": MagicMock})
