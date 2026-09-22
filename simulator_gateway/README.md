# GX Simulator2 gateway

This local process isolates MX Component from the Python application.
It binds only to `127.0.0.1`, requires a per-process token for every mutating
endpoint, and fixes `ActProgType.ActUnitType` to `UNIT_SIMULATOR2 (0x30)`.
Consequently it has no route that can target a physical PLC.

The adapter targets FX3U/FX3UC (`ActCpuType = 0x208`) and uses
`ActTargetSimulator = 0`, as required for an FX CPU. Tests may write only
ordinary X, M and D devices; Y and CPU-owned special devices are read-only.

Build it outside the repository:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_simulator_gateway.ps1
```

At runtime set a random token of at least 16 characters in
`GX_SIMULATOR_GATEWAY_TOKEN`, then start the compiled executable. The Python
client uses the same environment variable. The gateway exposes only:

- `GET /health`
- `POST /connect`
- `POST /disconnect`
- `POST /devices/read`
- `POST /devices/write`
- `POST /shutdown`

The design follows Mitsubishi Electric's MX Component programming manual:
GX Simulator2 uses `UNIT_SIMULATOR2 (0x30)`; `GetDevice` and `SetDevice` are
the documented single-device operations.

## Implementation and field owners

[Program.cs](Program.cs) defines gateway configuration, native target constants, token handling and HTTP operations. [NativeRequest.cs](../native_adapters/NativeRequest.cs) owns the native request envelope compiled into the helper. Rebuild with [build_simulator_gateway.ps1](../tools/build_simulator_gateway.ps1) when updating the Python/native protocol.

[plc.device_policy](../src/plc/device_policy.py) owns device and stimulus policy; the helper executes the prepared native plan. [Language boundaries](../docs/architecture/language-boundaries.md) define the Python/Core and C# adapter responsibilities. Physical PLC reading uses the [separate reader](../hardware_reader/README.md).
