# CodebaseMemory 調査ログ（2026-08-30）

```text
Repository: C:\VSC\SolerControler
Project: C-VSC-SolerControler

$ codex mcp get codebase-memory-mcp
enabled=true
transport=stdio
command=C:\tools\nodejs\codebase-memory-mcp.cmd

$ codebase-memory-mcp --version
codebase-memory-mcp 0.10.8

$ codebase-memory-mcp cli index_status --project C-VSC-SolerControler
nodes=5783, edges=18013, status=ready, parse_partial=9, skipped=0

$ codebase-memory-mcp cli get_graph_schema --project C-VSC-SolerControler
CALLS=4445, IMPORTS=1253, USAGE=1281, SIMILAR_TO=17,
SEMANTICALLY_RELATED=184, CALL_REFERENCE=30

$ codebase-memory-mcp cli query_graph ... inbound=0 candidates
Function=40, Method=6

$ rg -n "_clip_float|_estimate_required_charge_kwh|_daily_from_hourly|_archive_weather_rows|_provider_order_from_env|_parse_time|_parse_forecast_solar_time|_run_optional" app scripts tests
definition-only strong candidates=8

$ codebase-memory-mcp cli query_graph ... SIMILAR_TO
pairs=17, jaccard_1.000=14, jaccard_0.953=3

$ codebase-memory-mcp cli query_graph ... CALLS confidence < 0.5
low_confidence_calls=692, suffix_match=355, unique_name=337
builtins_targets=240, tests_targets=215

$ codebase-memory-mcp cli index_status --project C-VSC-SolerControler
after report hygiene: nodes=5625, edges=17857, status=ready
docs/completed/reports excluded by .cbmignore

After the four verified private-helper cleanups:
nodes=5621, edges=17809, status=ready
the four deleted helper nodes are absent from the graph

$ git diff --check
OK (line-ending warnings for existing Markdown files only)
```

秘密情報（`.env` の値、token、credential）は記録していない。ログは追跡対象にするため `.md` 形式で保存している。
