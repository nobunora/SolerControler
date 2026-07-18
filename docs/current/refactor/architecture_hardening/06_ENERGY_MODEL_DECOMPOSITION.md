# Phase 06: Energy-Model Decomposition

## Objective

Reduce responsibility concentration in `energy_model_main.py` without changing forecasts, optimization results, fallback order, or output documents.

Decompose by use case and external boundary. Do not rewrite the model or optimization algorithms.

## Prerequisites

Phase 05 must be completed.

Read only:

1. `AGENTS.md`
2. `00_EXECUTION_PROTOCOL.md`
3. This file
4. The latest Phase 05 handoff in `PROGRESS.md`
5. Existing modules under `app/energy_plan`
6. Direct symbol ranges and tests named by the handoff

Locate targets:

    rg -n "^(def|class) |AppConfig|_run_soc_optimization|evaluate_soc_candidate|optimize_soc_by_expected_cost|weather|forecast|energy_plan|write_" energy_model_main.py app tests

Do not read all of `energy_model_main.py`.

## Contracts to preserve

Preserve:

- Public CLI and callable entrypoints
- Environment keys and defaults
- Input paths and formats
- Forecast source priority
- Historical fallback behavior
- Timezone and date boundaries
- Units and rounding
- Missing and zero behavior
- SoC bounds and safety constraints
- Candidate generation and tie-breaking
- Cost calculations
- Diagnostics
- Energy-plan output paths and field names
- Existing failure behavior

Algorithm changes require a separate proposal after this phase.

## Target architecture

Use this flow:

    composition root
        -> focused settings
        -> external input ports
        -> typed planning context
        -> forecast calculations
        -> constraint preparation
        -> optimization orchestration
        -> typed plan result
        -> existing serializer and writer

Pure modules own calculations.

Adapters own:

- Environment parsing
- File access
- Weather and forecast retrieval
- Database access
- Current time
- Output writing

## Non-goals

Do not:

- Tune coefficients
- Change forecast formulas
- Change optimizer search space
- Change tariff rules
- Change SoC limits
- Change fallback order
- Replace the optimizer
- Add asynchronous execution
- Add a dependency-injection framework
- Replace configuration in one big step
- Reformat the entire module
- Move code only to reduce line count

## Step 06.1: Freeze entrypoint behavior

Characterize:

- Required and optional inputs
- Default paths and environment values
- Missing-input behavior
- Forecast source selection
- Historical fallback
- Optimization enabled and disabled paths
- Output writing
- Diagnostics
- Exit and exception behavior

Relevant tests include:

    tests/test_energy_model.py
    tests/test_energy_model_runtime.py
    tests/test_energy_plan_document.py
    tests/test_soc_cost_optimizer.py

Run them in small groups.

## Step 06.2: Build a symbol ownership map

Assign each target symbol to one owner:

- Composition
- Settings
- Input adapter
- Forecast calculation
- Historical profile
- Planning context
- Constraint preparation
- Optimization
- Output serialization
- Diagnostics

Record the map in the handoff.

Do not move a symbol until its inputs, outputs, callers, and direct tests are known.

For mixed functions, extract the pure inner calculation first.

## Step 06.3: Extract pure leaf helpers

Start with helpers that:

- Have no I/O
- Do not read environment variables
- Do not read current time
- Do not mutate global state
- Have narrow inputs and outputs
- Have direct tests or simple characterization cases

For each helper:

1. Add direct tests.
2. Move the implementation to a focused module.
3. Keep a compatibility wrapper when imports may exist.
4. Migrate one caller.
5. Run nearest tests.
6. Remove the old body only after all callers migrate.

Do not create a generic helper module for unrelated calculations.

## Step 06.4: Separate weather and forecast access

Create narrow ports for actual planning needs.

Examples:

    load_weather_forecast(...)
    load_pv_forecast(...)
    load_historical_profile(...)

Preserve:

- Source priority
- Timeout and retry behavior
- Missing-data fallback
- Timestamp normalization
- Units

Keep network exceptions at adapter boundaries.

Tests must use fakes or fixtures, not real network calls.

Forecast conversion and aggregation should be pure after raw data mapping.

## Step 06.5: Create typed planning context

Replace repeated parameter groups only when they form stable concepts.

Possible models:

- Planning horizon
- Current energy state
- Forecast series
- Consumption profile
- Battery constraints
- Tariff context
- Optimization request
- Optimization result

Requirements:

- One clear meaning per model
- Immutable when practical
- Explicit unit suffixes where needed
- No clients, loggers, or file I/O
- No universal context containing every setting and intermediate value

Preserve external field names in serializers.

## Step 06.6: Split `AppConfig` gradually

Migrate one cohesive field group at a time:

1. Forecast and weather settings
2. Historical-input settings
3. Battery and SoC settings
4. Tariff and cost settings
5. Output and diagnostic settings

For each group:

- Record keys and defaults
- Record blank-string behavior
- Record conversion errors
- Add a focused immutable settings model
- Parse once at the composition boundary
- Retain a legacy adapter while callers remain

Do not duplicate environment parsing permanently.

## Step 06.7: Isolate historical-profile calculation

Separate:

1. Loading historical records
2. Timestamp and unit normalization
3. Period selection
4. Hourly or daily profile calculation
5. Existing fallback rules

Preserve:

- Included hours
- Day classification
- Missing-hour behavior
- Weighting and averaging
- Rounding
- Minimum sample rules
- Existing fallback values

Add hour and date boundary tests.

## Step 06.8: Isolate forecast transformation

Separate raw retrieval from energy conversion.

The pure transformation receives:

- Typed source values
- Time range
- Conversion parameters
- Capacity and efficiency values
- Fallback inputs

Preserve:

- Timestamp alignment
- Horizon truncation
- Missing interval behavior
- Negative-value handling
- Unit conversion
- Daylight or hour filters
- Fallback order

Compare old and new series element by element.

## Step 06.9: Reduce optimizer parameter fan-out

Do not redesign optimization.

For `evaluate_soc_candidate` and `optimize_soc_by_expected_cost`:

1. Record each parameter's meaning and unit.
2. Group only cohesive parameters.
3. Add typed request models.
4. Preserve defaults and behavior.
5. Retain wrappers with the old signatures.
6. Migrate callers gradually.
7. Compare candidate scores and selected results exactly.

Do not change floating-point operation order without parity evidence.

## Step 06.10: Extract optimization orchestration

Separate:

- Request construction
- Candidate generation
- Candidate evaluation
- Selection and tie-breaking
- Diagnostics
- Disabled or unavailable fallback

The orchestration function must not read files, environment variables, or write output.

Required cases:

- Optimization disabled
- Empty candidate set
- Infeasible candidates
- Equal-cost tie
- Constraint boundary
- Missing forecast input
- Existing exception fallback

## Step 06.11: Separate output writing

Use existing `app/energy_plan` models when possible.

Separate:

1. Typed plan result
2. Serialization to current field names
3. Output-path selection
4. File write behavior
5. Diagnostics

Preserve:

- Filename and directory behavior
- JSON encoding
- Field names and value types
- Missing optional fields
- Write-failure behavior

Compare serialized output with existing fixtures.

## Step 06.12: Thin the composition root

The final entrypoint should primarily:

1. Parse focused settings.
2. Obtain current time.
3. Construct adapters.
4. Load and map inputs.
5. Build planning context.
6. Run forecast and optimization use cases.
7. Serialize and write the result.
8. Report diagnostics and failures.

It should not contain detailed formulas or duplicate fallback calculations.

Keep compatibility entrypoints.

## Error-handling rule

At external boundaries:

- Catch known external failures.
- Preserve original causes.
- Translate to focused errors or existing fallback results.
- Keep diagnostics compatible.
- Do not hide programming errors as empty data.

Pure functions should not catch broad exceptions to manufacture fallback values unless that is existing domain behavior.

## Required checks

Run in small groups:

    python -m pytest -q tests/test_energy_model.py
    python -m pytest -q tests/test_energy_model_runtime.py
    python -m pytest -q tests/test_soc_cost_optimizer.py
    python -m pytest -q tests/test_energy_plan_document.py

Then:

    python -m compileall -q app energy_model_main.py
    git diff --check

Run targeted mypy for each new module when available.

## Commit strategy

Use one conceptual extraction per commit.

Recommended order:

1. Characterization tests
2. Pure helper family
3. External port
4. Focused settings group
5. Historical profile
6. Forecast transformation
7. Optimization request models
8. Optimization orchestration
9. Output writer
10. Composition-root cleanup

Do not combine algorithm movement, configuration migration, and output migration.

## Stop conditions

Stop when:

- Numerical results differ unexpectedly.
- Floating-point operation order changes without proof.
- Fallback order is unclear.
- A model needs unrelated optional fields.
- External access is required for validation.
- Output schema lacks tests.
- A parameter's meaning or unit is unclear.
- Operations or dashboard contracts would change.
- Targeted tests already fail.
- The next safe edit is not one focused ownership change.

## Completion gate

Phase 06 is complete only when:

- `energy_model_main.py` primarily coordinates use cases and adapters.
- Pure forecast and planning calculations have focused modules.
- External access is behind narrow boundaries.
- `AppConfig` responsibility is reduced through focused settings.
- Optimizer callers use cohesive requests or compatibility wrappers.
- Output serialization preserves the document schema.
- Numerical parity and fallback tests remain.
- Targeted tests, compile checks, and `git diff --check` pass.
- Each conceptual change has a focused commit.
- `PROGRESS.md` records remaining debt for final integration.

## Required handoff to Phase 07

Provide:

- New modules and ownership
- Focused settings groups
- External ports
- Compatibility wrappers
- Numerical parity tests
- Output-schema tests
- Remaining large functions and justification
- Known mypy errors in changed scope
- Final integration test groups
- Files Phase 07 must not reread
