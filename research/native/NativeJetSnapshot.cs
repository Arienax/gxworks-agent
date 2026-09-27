// Read-only Jet schema/row evidence from explicitly extracted compiler caches.
// Inputs must be copies in the experiment directory. The optional cache-format
// key comes from the hash-bound installed native CreateConnectString routine,
// never from user/project protection settings; it is not emitted in results.
using System;
using System.Collections.Generic;
using System.Data;
using System.Data.OleDb;
using System.IO;
using System.Web.Script.Serialization;

class NativeJetSnapshot {
    static object Value(object value) {
        if(value==DBNull.Value)return null;
        if(value is byte[])return new {kind="bytes",base64=Convert.ToBase64String((byte[])value)};
        if(value is DateTime)return ((DateTime)value).ToString("o");
        return value;
    }
    static List<object> Rows(DataTable table) {
        var result=new List<object>();
        foreach(DataRow row in table.Rows) {
            var item=new Dictionary<string,object>();
            foreach(DataColumn column in table.Columns)item[column.ColumnName]=Value(row[column]);
            result.Add(item);
        }
        return result;
    }
    static void Main(string[] args) {
        if(args.Length!=2)throw new Exception("Explicit copied input and new output required");
        string source=Path.GetFullPath(args[0]),output=Path.GetFullPath(args[1]);
        if(File.Exists(output)||Path.GetDirectoryName(source)!=Path.GetDirectoryName(output))throw new Exception("Use a copied cache in its new experiment directory");
        var result=new Dictionary<string,object>();
        result["provider"]="Microsoft.Jet.OLEDB.4.0";result["mode"]="Read";
        try {
            var builder=new OleDbConnectionStringBuilder();
            builder.Provider="Microsoft.Jet.OLEDB.4.0";builder.DataSource=source;builder["Mode"]="Read";
            string cacheKey=Environment.GetEnvironmentVariable("GXW_NATIVE_JET_CACHE_KEY");
            if(!String.IsNullOrEmpty(cacheKey))builder["Jet OLEDB:Database Password"]=cacheKey;
            using(var connection=new OleDbConnection(builder.ConnectionString)) {
                connection.Open();
                DataTable schema=connection.GetOleDbSchemaTable(OleDbSchemaGuid.Tables,null);
                result["tables_schema"]=Rows(schema);
                result["columns_schema"]=Rows(connection.GetOleDbSchemaTable(OleDbSchemaGuid.Columns,null));
                var tables=new List<object>();result["tables"]=tables;
                foreach(DataRow row in schema.Rows) {
                    if((string)row["TABLE_TYPE"]!="TABLE")continue;
                    string name=(string)row["TABLE_NAME"];
                    var table=new DataTable();
                    using(var command=new OleDbCommand("SELECT TOP 100001 * FROM ["+name.Replace("]","]]")+"]",connection))
                    using(var reader=command.ExecuteReader())table.Load(reader);
                    if(table.Rows.Count>100000)throw new Exception("Table row bound exceeded");
                    tables.Add(new {name=name,rows=Rows(table)});
                }
            }
        } catch(Exception e) {
            result["error"]=new {type=e.GetType().FullName,message=e.Message};
            Environment.ExitCode=2;
        }
        var json=new JavaScriptSerializer();json.MaxJsonLength=128*1024*1024;
        File.WriteAllText(output,json.Serialize(result));
    }
}
