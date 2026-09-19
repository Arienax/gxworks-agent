# GX Simulator2 gateway

This small local process isolates MX Component from the Python application.
It binds only to `127.0.0.1`, requires a per-process token for every mutating
endpoint, and fixes `ActProgType.ActUnitType` to `UNIT_SIMULATOR2 (0x30)`.
Consequently it has no route that can target a physical PLC.

The current implementation targets FX3U/FX3UC (`ActCpuType = 0x208`) and uses
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

## Adapter boundary

C# is vendor/native only. Python Core prepares device names and values, including address policy and T/C current-value mapping. This build uses native-plan protocol v3; rebuild the helper with its existing PowerShell build script when upgrading Python. `native_adapters/NativeRequest.cs` is compiled with it. Existing read-only / Simulator2-only routes and transport protections remain; no PLC rule tables should be reintroduced here.
