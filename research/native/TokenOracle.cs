// Offline byte conversion only. This helper has no project, UI or device APIs.
// ABI recovered from ECCodeGeneratorFX2.dll 15.31; the Python caller pins its hash.
using System;
using System.Runtime.InteropServices;

public static class TokenOracle {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);
    [DllImport("kernel32.dll", CharSet=CharSet.Ansi, ExactSpelling=true)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate IntPtr NewObject();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int OpenObject(IntPtr h, int cpu, int mode);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int CloseObject(IntPtr h);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void DeleteObject(IntPtr h);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int Decode(IntPtr h,
        [In] byte[] input, ref int inputSize, [Out] byte[] output, ref int outputSize);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int Encode(IntPtr h,
        [In] byte[] input, int inputSize, int option1, int option2,
        [Out] byte[] output, ref int outputSize, ref int lastKind, IntPtr context);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int StoredSteps(IntPtr h,
        int inputSize, [In] byte[] input, ref int steps);
    // Private 15.31 RVA, scoped to one caller-bounded instruction fragment.
    // Native code may expand/contract a basic header by one byte. The Python
    // harness pins the DLL hash; this is an experiment, not a stable vendor API.
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate short RecomputeWidth(
        [In, Out] byte[] fragment, int inputSize);
    [StructLayout(LayoutKind.Sequential, Pack=1)] struct CodeBuffers {
        public IntPtr output;
        public int remaining;
        public IntPtr input;
        public IntPtr deviceOffsets;
        public int reserved16, reserved20;
    }
    // ChangePToMcode -> vtable 0x622D0 slot 1 -> RVA 0x8947.
    // Six 32-bit fields are copied by RVA 0x557A; pointer deltas give byte
    // counts without assuming a machine-word size or interpreting instructions.
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int Machinecode(
        IntPtr h, ref CodeBuffers buffers, IntPtr deviceContext, ref int words,
        int option1, int option2);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int ErrorOffset(
        IntPtr h, ref int offset);
    // The export copies two six-byte values, each occupying eight stack bytes.
    // The inspected FX path (RVA 0x2FAEF -> 0x328FD) does not use these values.
    [StructLayout(LayoutKind.Sequential, Pack=1)] struct PackedNativeValue {
        public uint low;
        public ushort high;
    }
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int FromMachinecode(
        IntPtr h, ref CodeBuffers buffers, IntPtr deviceContext, ref int bytes,
        int option1, int option2, PackedNativeValue value1, PackedNativeValue value2);

    static T Export<T>(IntPtr module, string name) where T : class {
        IntPtr address = GetProcAddress(module, name);
        if (address == IntPtr.Zero) throw new Exception("Missing export: " + name);
        return Marshal.GetDelegateForFunctionPointer(address, typeof(T)) as T;
    }

    public static int Main(string[] args) {
        if (IntPtr.Size != 4 || args.Length != 3) return 2;
        IntPtr module = LoadLibraryEx(args[0], IntPtr.Zero, 8);
        if (module == IntPtr.Zero) throw new Exception("LoadLibraryEx: " + Marshal.GetLastWin32Error());
        NewObject create = Export<NewObject>(module, "ObjectNew");
        OpenObject open = Export<OpenObject>(module, "Open");
        CloseObject close = Export<CloseObject>(module, "Close");
        DeleteObject destroy = Export<DeleteObject>(module, "ObjectDelete");
        Decode decode = Export<Decode>(module, "ChangePToILcode");
        Encode encode = Export<Encode>(module, "ChangeILToPcode");
        StoredSteps storedSteps = Export<StoredSteps>(module, "GetStepSize");
        Machinecode machinecode = Export<Machinecode>(module, "ChangePToMcode");
        FromMachinecode fromMachinecode = Export<FromMachinecode>(module, "ChangeMToPcode");
        ErrorOffset getErrorOffset = Export<ErrorOffset>(module, "GetErrorOffset");
        RecomputeWidth recomputeWidth = Marshal.GetDelegateForFunctionPointer(
            IntPtr.Add(module, 0x3E534), typeof(RecomputeWidth)) as RecomputeWidth;
        IntPtr handle = create();
        if (handle == IntPtr.Zero) throw new Exception("ObjectNew failed");
        try {
            int result = open(handle, int.Parse(args[1]), 0);
            if (result != 0) throw new Exception("Open: " + result.ToString("X8"));
            string line;
            while ((line = Console.ReadLine()) != null) {
                string[] fields = line.Split('\t');
                if (fields.Length != 3) throw new Exception("Invalid request framing");
                int id = int.Parse(fields[1]);
                byte[] input = Convert.FromBase64String(fields[2]);
                if (input.Length == 0 || input.Length > 32768) throw new Exception("Input size outside tested envelope");
                byte[] output = new byte[1048576];
                int inputSize = input.Length, outputSize = output.Length, lastKind = 0, nativeValue = 0;
                if (fields[0] == "decode") result = decode(handle, input, ref inputSize, output, ref outputSize);
                else if (fields[0] == "encode") result = encode(handle, input, inputSize, int.Parse(args[2]), 0, output, ref outputSize, ref lastKind, IntPtr.Zero);
                else if (fields[0] == "stored-steps") {
                    outputSize = 0;
                    result = storedSteps(handle, inputSize, input, ref nativeValue);
                }
                else if (fields[0] == "machinecode") {
                    GCHandle inputPin = GCHandle.Alloc(input, GCHandleType.Pinned);
                    GCHandle outputPin = GCHandle.Alloc(output, GCHandleType.Pinned);
                    // RVA 0x400AD initializes 0x80 bytes through field +0x0C.
                    GCHandle offsetsPin = GCHandle.Alloc(new byte[128], GCHandleType.Pinned);
                    try {
                        IntPtr inputStart = inputPin.AddrOfPinnedObject(), outputStart = outputPin.AddrOfPinnedObject();
                        CodeBuffers buffers = new CodeBuffers();
                        buffers.input = inputStart;
                        buffers.remaining = inputSize;
                        buffers.output = outputStart;
                        buffers.deviceOffsets = offsetsPin.AddrOfPinnedObject();
                        nativeValue = output.Length / 4;
                        result = machinecode(handle, ref buffers, IntPtr.Zero, ref nativeValue, 0, 0);
                        inputSize = buffers.input.ToInt32() - inputStart.ToInt32();
                        outputSize = buffers.output.ToInt32() - outputStart.ToInt32();
                    } finally {
                        offsetsPin.Free();
                        outputPin.Free();
                        inputPin.Free();
                    }
                }
                else if (fields[0] == "from-machinecode") {
                    GCHandle inputPin = GCHandle.Alloc(input, GCHandleType.Pinned);
                    GCHandle outputPin = GCHandle.Alloc(output, GCHandleType.Pinned);
                    GCHandle offsetsPin = GCHandle.Alloc(new byte[128], GCHandleType.Pinned);
                    try {
                        IntPtr inputStart = inputPin.AddrOfPinnedObject();
                        CodeBuffers buffers = new CodeBuffers();
                        // Buffer roles are reversed for the M -> P export.
                        buffers.output = inputStart;
                        buffers.remaining = inputSize;
                        buffers.input = outputPin.AddrOfPinnedObject();
                        buffers.deviceOffsets = offsetsPin.AddrOfPinnedObject();
                        nativeValue = output.Length;
                        result = fromMachinecode(handle, ref buffers, IntPtr.Zero, ref nativeValue,
                            0, 0, new PackedNativeValue(), new PackedNativeValue());
                        inputSize = buffers.output.ToInt32() - inputStart.ToInt32();
                        // RVA 0x88BD returns the accumulated byte count. The
                        // copied +8 pointer refers to a temporary native buffer.
                        outputSize = result == 0 ? nativeValue : 0;
                    } finally {
                        offsetsPin.Free();
                        outputPin.Free();
                        inputPin.Free();
                    }
                }
                else if (fields[0] == "recompute-width") {
                    if (inputSize > 32766) throw new Exception("Fragment length exceeds native signed-short result");
                    // Enter through the object's export once to select its CPU
                    // context before calling the internal memory-only routine.
                    byte[] end = new byte[] { 3, 0x34, 3, 0 };
                    int endSize = end.Length, warmSize = output.Length;
                    result = decode(handle, end, ref endSize, output, ref warmSize);
                    if (result != 0) throw new Exception("CPU-context initialization failed");
                    Array.Clear(output, 0, output.Length);
                    Array.Copy(input, output, inputSize);
                    nativeValue = recomputeWidth(output, inputSize);
                    result = nativeValue < 0 ? nativeValue : 0;
                    outputSize = nativeValue < 0 ? 0 : nativeValue;
                }
                else throw new Exception("Unsupported operation");
                if (result == 0 && (outputSize < 0 || outputSize > output.Length)) throw new Exception("Invalid returned output size");
                string payload = result == 0 ? Convert.ToBase64String(output, 0, outputSize) : "";
                int errorOffset = -1;
                int errorStatus = result == 0 ? 0 : getErrorOffset(handle, ref errorOffset);
                Console.WriteLine("{\"id\":" + id + ",\"return_code\":\"0x" + result.ToString("X8")
                    + "\",\"consumed_bytes\":" + inputSize + ",\"output_bytes\":" + outputSize
                    + ",\"last_kind\":" + lastKind + ",\"native_value\":" + nativeValue
                    + ",\"error_offset\":" + (result == 0 || errorStatus != 0 ? "null" : errorOffset.ToString())
                    + ",\"output_base64\":\"" + payload + "\"}");
                Console.Out.Flush();
            }
        } finally { close(handle); destroy(handle); }
        return 0;
    }
}
