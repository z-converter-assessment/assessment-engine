# Server Classification Rules — Assessment Engine

The engine uses USE Method-derived rules to classify each server into one of seven
right-sizing categories. Classification is deterministic (no LLM) — `recommendation.py`
implements these rules. AI narratives reference these labels but never override them.

## Categories

### over_provisioned

- CPU p95 < 30% over 14-day window
- Memory p95 < 50%
- iowait p95 < 5%
- No swap usage
- Action: downsize_cpu — reduce instance type (vCPU and/or memory)

This category typically reflects servers sized for peak load that never materializes,
or workloads that have shrunk over time but the instance type was not updated.

### under_provisioned

- CPU p95 > 90% OR memory p95 > 90% sustained
- Action: upsize — increase instance type

Under-provisioning manifests as sustained high utilization. Users experience latency
and unpredictable performance. Upsize urgency depends on the severity and the
workload's business criticality.

### idle

- CPU p95 < 5% with no clear business hours pattern
- No network activity OR minimal background activity only
- Action: shutdown_review — manual confirmation before termination

Idle servers waste budget but may have business reasons (compliance, backup, standby).
Engineers should investigate before automatically terminating.

### shutdown

- CPU p95 < 3% sustained
- No network activity at all (no internal connections, no health checks)
- Action: shutdown_idle — terminate after engineer confirmation

This is the strongest signal that a server is no longer needed. Often the result of
project completion or service migration where the original instance was forgotten.

### cpu_high

- CPU p95 in 80-90% range
- Memory p95 < 80%
- Indicates CPU is the primary bottleneck
- Action: upsize_cpu (more vCPUs) OR investigate workload optimization

### mem_high

- Memory p95 in 80-90% range
- CPU p95 < 70%
- Memory is the bottleneck
- Action: upsize_memory OR investigate memory leaks

### optimal

- CPU p95 in 30-70% range
- Memory p95 in 50-80% range
- No swap, low iowait
- Action: no_action — current sizing is appropriate

Optimal servers strike the right balance between utilization and headroom. They handle
normal load comfortably and have capacity for predictable spikes.

### insufficient_data

- Evaluation window has < 7 days of metrics
- Metrics gaps (collection failures) make p95 unreliable
- Action: collect more data, do not act

This category is the safety net — when in doubt, do not recommend a change.

## Window Selection

The 14-day evaluation window is the AWS Compute Optimizer standard. It captures:

- Weekly cycles (weekday vs weekend load patterns)
- Maintenance windows
- Most cron-triggered batch loads (daily/weekly cron jobs)

Shorter windows (1-3 days) miss patterns and react to noise.
Longer windows (30+ days) react slowly to recent workload changes (e.g., a feature
launch that increased load 2 weeks ago).

Default window: 14 days. Operators may override per evaluation.

## Combined Signals

Classification considers metrics in priority order:

1. errors > 0 → investigate first (mask real load)
2. swap_used sustained → upsize_memory (regardless of CPU)
3. iowait > 15% sustained → disk_bottleneck (do not just upsize CPU)
4. CPU p95 > 90% → under_provisioned (urgent)
5. memory p95 > 90% → under_provisioned (urgent)
6. CPU p95 < 3% AND no network → shutdown
7. CPU p95 < 30% AND mem_p95 < 50% → over_provisioned
8. CPU p95 in 30-70% AND mem_p95 in 50-80% → optimal
9. Otherwise → mixed signals / requires engineer review

## How AI Narrative References These Rules

The LLM (ollama) receives a payload containing computed statistics and the classification
result. Its job is to explain the diagnosis in Korean narrative form — referencing the
classification label and recommended action verbatim, citing the actual percentages from
the payload.

Strict rule: every number in the narrative must come from the payload. The engine validates
this with regex extraction (see ADR 0003 section 3G).
