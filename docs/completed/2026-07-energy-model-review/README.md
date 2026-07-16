# Energy Model Forecast Gap Review

## Purpose

This document set describes verified issues and recommended changes for the solar consumption forecasting and overnight battery planning pipeline.

Repository:

`C:\VSC\SolerControler`

## Scope

Immediate implementation:

1. Correct hourly load profiles built from 30-minute energy records.
2. Remove the production 2.0 kWh cap from the overnight discharge guard.

Design proposals:

3. Improve weather archive retrieval diagnostics and resilience.
4. Prevent high-temperature correction from reducing load forecasts unexpectedly.

Start with [00_INDEX.md](00_INDEX.md).
