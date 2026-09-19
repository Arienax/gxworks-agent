using System;
using System.Collections.Generic;

// Wire/ABI validation only. PLC address meaning, canonicalization, permissions,
// bit widths and reset selection are prepared by the Python Core.
namespace PlcAi.NativeAdapter
{
    internal sealed class DeviceCall
    {
        public string Key;
        public string Device;
        public int Value;
    }

    internal static class NativeRequest
    {
        public static void Fields(IDictionary<string, object> value, params string[] fields)
        {
            if (value == null || value.Count != fields.Length) throw new ArgumentException("invalid_request_fields");
            foreach (string field in fields)
                if (!value.ContainsKey(field)) throw new ArgumentException("missing_request_field");
        }

        public static int Integer(object value)
        {
            // JavaScriptSerializer distinguishes integer, float and boolean.
            // Convert.ToInt32 would silently round floats and coerce booleans.
            if (!(value is int)) throw new ArgumentException("invalid_native_integer");
            return (int)value;
        }

        public static void Version(IDictionary<string, object> value, int expected)
        {
            object version;
            if (!value.TryGetValue("protocol_version", out version) || Integer(version) != expected)
                throw new ArgumentException("incompatible_native_protocol");
        }

        private static string Identifier(object value)
        {
            string text = value as string;
            if (String.IsNullOrEmpty(text)) throw new ArgumentException("invalid_native_identifier");
            return text;
        }

        public static List<DeviceCall> Devices(object raw, bool withValue, int maximum, bool allowEmpty, bool uniqueKeys = false)
        {
            object[] entries = raw as object[];
            if (entries == null || entries.Length > maximum || (!allowEmpty && entries.Length == 0))
                throw new ArgumentException("invalid_native_plan_size");
            List<DeviceCall> result = new List<DeviceCall>();
            HashSet<string> keys = new HashSet<string>(StringComparer.Ordinal);
            foreach (object entry in entries)
            {
                IDictionary<string, object> fields = entry as IDictionary<string, object>;
                if (withValue) Fields(fields, "key", "device", "value");
                else Fields(fields, "key", "device");
                DeviceCall call = new DeviceCall();
                call.Key = Identifier(fields["key"]);
                call.Device = Identifier(fields["device"]);
                if (withValue) call.Value = Integer(fields["value"]);
                if (uniqueKeys && !keys.Add(call.Key)) throw new ArgumentException("duplicate_native_call");
                result.Add(call);
            }
            return result;
        }
    }
}
