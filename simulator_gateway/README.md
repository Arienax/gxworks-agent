# GX Simulator2 Gateway

The GX Simulator2 Gateway is an isolated local helper process that decouples Mitsubishi MX Component and GX Simulator2 COM interop from the Python application runtime.

To maintain strict security isolation, the gateway:
- Binds strictly to the loopback interface (`127.0.0.1`);
- Requires a cryptographically random, per-process bearer token for every endpoint except `/health`;
- Hardcodes `ActProgType.ActUnitType` to `UNIT_SIMULATOR2` (`0x30`), completely preventing any network route from reaching physical PLC hardware.

Protocol version 3 binds `ActCpuType` and the expected returned model code to the CPU selected by Python Core. The legacy empty connection request defaults to FX3U/FX3UC (`ActCpuType = 0x208`, expected model code `0xF3`). The adapter sets `ActTargetSimulator = 0`. Test stimulus writes are strictly confined to ordinary `X`, `M`, and `D` devices; output coils (`Y`) and CPU-owned special registers are read-only.

### CPU selection

`connect_simulator(plc_model="Q06UDEH")` selects an explicitly registered FX/Q/L CPU. Omitting the model reuses an existing connected target; the initial legacy default remains FX3U. Discovery, device reads, RUN status and traces retain the connected model. A trace without `config.plc_model` inherits the target model; an explicitly different model is rejected.

The catalog in `resources/instructions/control_runtime.json` covers 55 programmable GX Works2 menu selections (42 Q, 5 L and 8 FX), including their combined-menu aliases. These are documented connection mappings, not a claim of live validation on every CPU or every MX Component edition. FX0S/FX0 and FX1 remain unavailable because their independent returned model codes have not been established; parameter-only remote I/O targets and Simulator3 CPUs are not included. MX Component connection errors remain visible.

`GET /health` returns the selected `cpu_type`, actual `observed_cpu_type` and `observed_cpu_name`. Some Q models share a returned model code, so Core also checks the returned CPU name for those entries. `ActCpuType` alone is a requested setting, not proof of the connected model. Rebuild the bundled gateway after this update; an older multi-model gateway without identity in health must be rebuilt before target discovery.

Simulator reads use the model's numbering, radix and address regions for X/Y/M/D/S/T/C, with T/C mapped to current-value devices TN/CN, plus explicitly registered RUN/error monitors. Simulator input plans remain limited to X/M/D. Physical-device access policies are unchanged. Address validity does not establish the current project's allocated memory; the native simulator can still reject an unallocated device.

CPU firmware-version checks remain opt-in. This model routing does not add default firmware checks.

## Building and Running

Compile the gateway executable outside the source tree:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_simulator_gateway.ps1
```

At runtime, generate a random token containing at least 16 characters, export it to the `GX_SIMULATOR_GATEWAY_TOKEN` environment variable, and start the compiled executable. The Python application runtime consumes this same environment variable to authenticate requests.

### Exposed HTTP Endpoints

- `GET /health` — Reports current process status and the `target_binding` identifier for any active simulator connection.
- `POST /connect` — Establishes a connection to the local GX Simulator2 instance and generates a fresh target binding.
- `POST /disconnect` — Gracefully disconnects from the simulator session.
- `POST /devices/read` — Reads discrete device values.
- `POST /observations/read` — Executes a structured observation plan prepared by Python Core (up to 64 devices) against the verified `target_binding`. Rejects the request if the connection target changed or disconnected. Does not write devices, alter CPU run states, or download programs.
- `POST /devices/write` — Writes permitted stimulus devices (`X`, `M`, `D`).
- `POST /shutdown` — Safely closes simulator connections and exits the gateway process.

### Verification and Observation Model

The `/observations/read` endpoint provides a deterministic observation channel for MCP discovery and timeline sampling, completely separated from static program diagnostics. It requires an exact protocol version match, a pre-validated read plan, and active target binding validation before reading device memory.

The gateway implementation conforms to Mitsubishi Electric's MX Component specifications: GX Simulator2 relies on `UNIT_SIMULATOR2` (`0x30`), with `GetDevice` and `SetDevice` serving as the standard single-device access operations.

## Architecture and Source Mapping

- **Native Gateway Implementation:** [Program.cs](Program.cs) defines gateway HTTP routes, configuration, target constants, and COM call sequencing.
- **Request Envelope:** [native_adapters/NativeRequest.cs](../native_adapters/NativeRequest.cs) defines the serialized request contracts shared between Python Core and native helpers. Rebuild with [tools/build_simulator_gateway.ps1](../tools/build_simulator_gateway.ps1) whenever protocol structures are updated.
- **Device Policy and Plan Preparation:** [src/plc/device_policy.py](../src/plc/device_policy.py) enforces device access rules, read limits, and stimulus safety.
- **Physical Device Reading:** For read-only inspection of physical PLCs, refer to the completely independent [hardware reader](../hardware_reader/README.md).

Architecturally, PLC domain rules, safety constraints, and observation plans are owned entirely by Python Core (`src/plc/` and `src/simulator/`). The C# gateway functions strictly as an unopinionated COM bridge and process-level isolation boundary.
