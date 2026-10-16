using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using PlcAi.NativeAdapter;
using System.Web.Script.Serialization;

// This helper is deliberately separate from the Simulator2 gateway. It has no
// server, write-device, CPU-state, reset, force, or generic invocation command.
namespace PlcAi.HardwareReader
{
    internal static class Program
    {
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 16384 };

        private static void Print(object value) { Console.WriteLine(Json.Serialize(value)); }

        private static Type ControlType()
        {
            string progId = IntPtr.Size == 8 ? "ActUtlType64.ActUtlType64" : "ActUtlType.ActUtlType";
            return Type.GetTypeFromProgID(progId, false);
        }

        [STAThread]
        private static int Main()
        {
            object control = null;
            bool opened = false;
            try
            {
                // Read at most one bounded request; stdin EOF ends the command.
                char[] buffer = new char[16385];
                int length = 0, count;
                while (length < buffer.Length && (count = Console.In.Read(buffer, length, buffer.Length - length)) > 0) length += count;
                if (length >= buffer.Length) throw new ArgumentException("request_too_large");
                Dictionary<string, object> request = Json.DeserializeObject(new string(buffer, 0, length)) as Dictionary<string, object>;
                if (request == null || !request.ContainsKey("operation")) throw new ArgumentException("invalid_request");
                if (Convert.ToString(request["operation"]) == "capabilities")
                {
                    if (request.Count != 1) throw new ArgumentException("invalid_request");
                    Print(new { status = "capabilities", backend = "mx_logical_station_read_only", mx_registered = ControlType() != null,
                                protocol_version = 2, device_read = true, device_write = false, persistent_connection = false });
                    return 0;
                }
                NativeRequest.Fields(request, "operation", "protocol_version", "logical_station", "devices");
                NativeRequest.Version(request, 2);
                if (Convert.ToString(request["operation"]) != "read") throw new ArgumentException("unsupported_operation");
                int station = NativeRequest.Integer(request["logical_station"]);
                if (station < 0 || station > 1023) throw new ArgumentException("invalid_station");
                List<DeviceCall> devices = NativeRequest.Devices(request["devices"], false, 64, false, true);
                Type type = ControlType();
                if (type == null) { Print(new { status = "error", code = "mx_not_registered" }); return 2; }
                control = Activator.CreateInstance(type);
                dynamic mx = control;
                mx.ActLogicalStationNumber = station;
                int openCode = Convert.ToInt32(mx.Open(), CultureInfo.InvariantCulture);
                if (openCode != 0) { Print(new { status = "error", code = "mx_open_failed" }); return 2; }
                opened = true;
                Dictionary<string, int> values = new Dictionary<string, int>();
                foreach (DeviceCall call in devices)
                {
                    string device = call.Device;
                    int data = 0;
                    int readCode = Convert.ToInt32(mx.GetDevice(device, out data), CultureInfo.InvariantCulture);
                    if (readCode != 0) { Print(new { status = "error", code = "mx_read_failed" }); return 2; }
                    values.Add(call.Key, data);
                }
                int closeCode = Convert.ToInt32(mx.Close(), CultureInfo.InvariantCulture);
                opened = false;
                if (closeCode != 0) { Print(new { status = "error", code = "mx_close_failed" }); return 2; }
                Print(new { status = "read", protocol_version = 2, backend = "mx_logical_station_read_only", logical_station = station, values = values });
                return 0;
            }
            catch (ArgumentException) { Print(new { status = "error", code = "invalid_request" }); return 2; }
            catch (Exception) { Print(new { status = "error", code = "reader_failed" }); return 2; }
            finally
            {
                if (control != null)
                {
                    if (opened) { try { ((dynamic)control).Close(); } catch (Exception) { } }
                    if (Marshal.IsComObject(control)) Marshal.FinalReleaseComObject(control);
                }
            }
        }
    }
}
