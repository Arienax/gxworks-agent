// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;

namespace PlcAi.NativeAdapter
{
    // Opt-in capture of the first-chance exceptions the vendor compiler raises while a
    // native operation runs. The vendor build path uses C++ exceptions for ordinary
    // control flow (a failed database read throws _com_error and is caught again), so a
    // rejection whose only report is 0x500c5150 means one of those throws escaped to the
    // vendor catch-all. The report code carries no cause; this trace records the throw
    // site so the next real occurrence identifies itself.
    // Nothing is installed and no handler is registered unless the operator names an
    // output file, so the ordinary path keeps its original behaviour and cost.
    static class NativeExceptionTrace
    {
        [StructLayout(LayoutKind.Sequential)]
        struct MemoryInfo
        {
            public IntPtr BaseAddress, AllocationBase;
            public uint AllocationProtect, RegionSize, State, Protect, Type;
        }

        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int Handler(IntPtr pointers);

        [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr AddVectoredExceptionHandler(uint first, Handler callback);
        [DllImport("kernel32.dll")] static extern uint VirtualQuery(IntPtr address, out MemoryInfo info, UIntPtr size);
        [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
        [DllImport("kernel32.dll")] static extern uint GetLastError();
        [DllImport("kernel32.dll")] static extern void SetLastError(uint value);

        const string Variable = "GXW_NATIVE_EXCEPTION_TRACE";
        const uint CppException = 0xe06d7363, AccessViolation = 0xc0000005;
        const int Limit = 64;

        static readonly List<object> records = new List<object>();
        static Handler callback;
        static IntPtr registration = IntPtr.Zero;
        [ThreadStatic] static bool busy;

        internal static bool Enabled { get { return registration != IntPtr.Zero; } }

        static IntPtr Pointer(uint value) { return new IntPtr(unchecked((int)value)); }
        static uint U32(IntPtr value) { return unchecked((uint)value.ToInt32()); }
        static string Hex(uint value) { return "0x" + value.ToString("x8"); }

        static int Available(uint address, int requested)
        {
            if (address < 0x10000) return 0;
            MemoryInfo info;
            if (VirtualQuery(Pointer(address), out info, (UIntPtr)28) != 28 || info.State != 0x1000 || (info.Protect & 0x101) != 0)
                return 0;
            long end = (long)U32(info.BaseAddress) + info.RegionSize;
            return (int)Math.Max(0, Math.Min(requested, end - address));
        }

        static uint Word(uint address) { return Available(address, 4) == 4 ? unchecked((uint)Marshal.ReadInt32(Pointer(address))) : 0; }

        static object[] Types(uint information)
        {
            var types = new List<object>();
            uint array = Word(information + 12), count = Word(array);
            if (count > 16) return types.ToArray();
            for (uint index = 0; index < count; index++)
            {
                uint type = Word(array + 4 + index * 4), descriptor = Word(type + 4);
                int size = Available(descriptor + 8, 256), length = 0;
                while (length < size && Marshal.ReadByte(Pointer(descriptor + 8), length) != 0) length++;
                types.Add(new { name = size > 0 ? Marshal.PtrToStringAnsi(Pointer(descriptor + 8), length) : "" });
            }
            return types.ToArray();
        }

        static int OnException(IntPtr pointers)
        {
            if (busy || registration == IntPtr.Zero) return 0;
            if (records.Count >= Limit) return 0;
            uint lastError = GetLastError();
            busy = true;
            try
            {
                uint record = Word(U32(pointers)), context = Word(U32(pointers) + 4);
                uint code = Word(record);
                if (code != CppException && code != AccessViolation) return 0;
                uint parameters = Word(record + 16);
                uint thrown = parameters >= 3 ? Word(record + 24) : 0;
                var frames = new List<string>();
                uint ebp = Word(context + 180);
                for (int depth = 0; depth < 16 && Available(ebp, 8) == 8; depth++)
                {
                    frames.Add(Hex(Word(ebp + 4)));
                    uint next = Word(ebp);
                    if (next <= ebp || next - ebp > 1024 * 1024) break;
                    ebp = next;
                }
                // Only an access violation carries the address that could not be read,
                // so that is the record worth expanding beyond the frame chain.
                var arguments = new List<string>();
                for (uint index = 0; index < Math.Min(parameters, 4); index++)
                    arguments.Add(Hex(Word(record + 20 + index * 4)));
                var registers = new Dictionary<string, string>();
                var container = new List<string>();
                if (code == AccessViolation)
                {
                    string[] names = { "edi", "esi", "ebx", "edx", "ecx", "eax", "ebp", "eip", "esp" };
                    uint[] offsets = { 156, 160, 164, 168, 172, 176, 180, 184, 196 };
                    for (int index = 0; index < names.Length; index++)
                        registers[names[index]] = Hex(Word(context + offsets[index]));
                    // The faulting read indexes a container through its data pointer, so the
                    // object itself explains the state: a count that disagrees with its storage.
                    uint source = Word(context + 156);
                    if (Available(source, 32) == 32)
                        for (uint index = 0; index < 8; index++)
                            container.Add(Hex(Word(source + index * 4)));
                    uint element = Word(context + 176);
                    if (Available(element, 16) == 16)
                        for (uint index = 0; index < 4; index++)
                            container.Add("->" + Hex(Word(element + index * 4)));
                }
                lock (records)
                    records.Add(new
                    {
                        code = Hex(code), address = Hex(Word(record + 12)), thread = GetCurrentThreadId(),
                        ticks = Environment.TickCount,
                        cpp_types = code == CppException && parameters == 3 ? Types(Word(record + 28)) : new object[0],
                        thrown = Hex(thrown), parameters = arguments.ToArray(), registers = registers,
                        container = container.ToArray(), frames = frames.ToArray()
                    });
            }
            catch { }
            finally { busy = false; SetLastError(lastError); }
            return 0; // continue the original dispatch and the vendor's own handler
        }

        internal static void Install()
        {
            string path = Environment.GetEnvironmentVariable(Variable);
            if (String.IsNullOrEmpty(path)) return;
            try
            {
                callback = OnException;
                registration = AddVectoredExceptionHandler(1, callback);
            }
            catch { registration = IntPtr.Zero; }
            if (registration == IntPtr.Zero)
                Console.Error.WriteLine("exception trace unavailable; the native operation continues unobserved");
        }

        internal static int Count { get { return records.Count; } }

        internal static void Flush(string outcome)
        {
            if (!Enabled) return;
            string path = Environment.GetEnvironmentVariable(Variable);
            try
            {
                var modules = new List<object>();
                foreach (ProcessModule module in Process.GetCurrentProcess().Modules)
                    modules.Add(new { name = module.ModuleName, address = Hex(U32(module.BaseAddress)), size = module.ModuleMemorySize });
                var json = new JavaScriptSerializer { MaxJsonLength = 4 * 1024 * 1024 };
                if (!String.IsNullOrEmpty(path))
                    File.WriteAllText(path, json.Serialize(new { installed = true, outcome = outcome,
                        modules = modules, exceptions = records }), new UTF8Encoding(false));
            }
            catch (Exception error) { Console.Error.WriteLine("exception trace: " + error.Message); }
        }
    }
}
