// Offline scalar formatting only. No GX project, compiler, device or PLC APIs.
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

internal static class LegacyRealOracle
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr LoadLibrary(string file);
    [DllImport("kernel32.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate IntPtr Gcvt(double value, int digits, [Out] byte[] output);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate IntPtr Ecvt(double value, int digits, out int decimalPoint, out int sign);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate int Sprintf([Out] byte[] output, [MarshalAs(UnmanagedType.LPStr)] string format, double value);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate double Atof([MarshalAs(UnmanagedType.LPStr)] string value);
    static string Hex(byte[] bytes) { return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }
    static byte[] Unhex(string text) { byte[] bytes = new byte[text.Length / 2]; for (int i = 0; i < bytes.Length; i++) bytes[i] = Convert.ToByte(text.Substring(i * 2, 2), 16); return bytes; }
    static string Z(byte[] bytes) { int n = Array.IndexOf(bytes, (byte)0); if (n < 0) throw new InvalidDataException("Unterminated output"); return Encoding.ASCII.GetString(bytes, 0, n); }
    static T Export<T>(IntPtr module, string name) where T : class { IntPtr p = GetProcAddress(module, name); if (p == IntPtr.Zero) throw new InvalidDataException("Missing export " + name); return Marshal.GetDelegateForFunctionPointer(p, typeof(T)) as T; }
    static int Main(string[] args)
    {
        if (args.Length != 3 || IntPtr.Size != 4) return 2;
        string dll = Path.GetFullPath(args[0]); IntPtr module = LoadLibrary(dll); if (module == IntPtr.Zero) throw new InvalidOperationException("LoadLibrary: " + Marshal.GetLastWin32Error());
        Gcvt gcvt = Export<Gcvt>(module, "_gcvt"); Ecvt ecvt = Export<Ecvt>(module, "_ecvt"); Sprintf sprintf = Export<Sprintf>(module, "sprintf"); Atof atof = Export<Atof>(module, "atof");
        List<object> rows = new List<object>();
        foreach (string line in File.ReadAllLines(args[1]))
        {
            if (line.Length != 16) throw new InvalidDataException("Expected binary64 hex");
            double input = BitConverter.ToDouble(Unhex(line), 0); if (double.IsInfinity(input) || double.IsNaN(input)) throw new InvalidDataException("Finite input required");
            byte[] buffer = new byte[512]; gcvt(input, 15, buffer); string first = Z(buffer); double parsed = atof(first);
            buffer = new byte[512]; sprintf(buffer, "%+7.6E", parsed); string second = Z(buffer); double final = atof(second);
            buffer = new byte[512]; sprintf(buffer, "%+7.6E", input); string direct = Z(buffer);
            int point, sign; string digits = Marshal.PtrToStringAnsi(ecvt(parsed, 17, out point, out sign));
            rows.Add(new { input_binary64_hex = line, gcvt15 = first, backend_input_binary64_hex = Hex(BitConverter.GetBytes(parsed)),
                sprintf7 = second, direct_sprintf7 = direct, rounded_binary64_hex = Hex(BitConverter.GetBytes(final)), binary32_hex = Hex(BitConverter.GetBytes((float)final)),
                ecvt17 = digits, ecvt17_point = point, ecvt17_sign = sign });
        }
        string digest; using (SHA256 hash = SHA256.Create()) digest = Hex(hash.ComputeHash(File.ReadAllBytes(dll)));
        JavaScriptSerializer json = new JavaScriptSerializer(); json.MaxJsonLength = Int32.MaxValue;
        File.WriteAllText(args[2], json.Serialize(new { dll = dll, dll_sha256 = digest, process_bits = 32, rows = rows }), new UTF8Encoding(false));
        return 0;
    }
}
