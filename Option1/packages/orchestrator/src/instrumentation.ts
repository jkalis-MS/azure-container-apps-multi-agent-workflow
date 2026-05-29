import { NodeSDK } from "@opentelemetry/sdk-node";
import { getNodeAutoInstrumentations } from "@opentelemetry/auto-instrumentations-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import { OTLPMetricExporter } from "@opentelemetry/exporter-metrics-otlp-proto";
import { PeriodicExportingMetricReader } from "@opentelemetry/sdk-metrics";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { ATTR_SERVICE_NAME } from "@opentelemetry/semantic-conventions";
import { AzureMonitorTraceExporter, AzureMonitorMetricExporter } from "@azure/monitor-opentelemetry-exporter";

const connString = process.env.APPLICATIONINSIGHTS_CONNECTION_STRING;
const otlpEndpoint = process.env.OTEL_EXPORTER_OTLP_ENDPOINT;

if (connString || otlpEndpoint) {
  const resource = resourceFromAttributes({
    [ATTR_SERVICE_NAME]: process.env.OTEL_SERVICE_NAME ?? "wc-orchestrator",
  });

  // Prefer Azure Monitor exporter if connection string is set
  const traceExporter = connString
    ? new AzureMonitorTraceExporter({ connectionString: connString })
    : new OTLPTraceExporter({ url: `${otlpEndpoint}/v1/traces` });

  const metricExporter = connString
    ? new AzureMonitorMetricExporter({ connectionString: connString })
    : new OTLPMetricExporter({ url: `${otlpEndpoint}/v1/metrics` });

  const sdk = new NodeSDK({
    resource,
    traceExporter,
    metricReader: new PeriodicExportingMetricReader({ exporter: metricExporter }),
    instrumentations: [
      getNodeAutoInstrumentations({
        "@opentelemetry/instrumentation-fs": { enabled: false },
      }),
    ],
  });

  sdk.start();
  console.log(`OTEL enabled → ${connString ? "Azure Monitor" : otlpEndpoint}`);
} else {
  console.log("OTEL disabled (no APPLICATIONINSIGHTS_CONNECTION_STRING or OTEL_EXPORTER_OTLP_ENDPOINT)");
}
