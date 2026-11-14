// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Management;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: TargetFramework(".NETFramework,Version=v4.7")]

sealed class LaunchSettings
{
    public int Port { get; set; }
    public string StateDirectory { get; set; }
    public bool ReadOnly { get; set; }
    public bool NoServiceDiscovery { get; set; }
}

sealed class WorkbenchProcess
{
    public int Id;
    public LaunchSettings Settings;
    public string StopEvent;
}

static class WindowsListeners
{
    // IPv4 MIB_TCPTABLE_OWNER_PID with TCP_TABLE_OWNER_PID_LISTENER.
    [DllImport("iphlpapi.dll")]
    static extern uint GetExtendedTcpTable(IntPtr table, ref int size, bool sort, int family, int tableClass, uint reserved);

    public static Dictionary<int, int> LocalPorts()
    {
        const int IPv4 = 2, OwnerPidListener = 3, RowSize = 24;
        int size = 0;
        GetExtendedTcpTable(IntPtr.Zero, ref size, false, IPv4, OwnerPidListener, 0);
        IntPtr table = Marshal.AllocHGlobal(size + 1024);
        try
        {
            size += 1024;
            uint status = GetExtendedTcpTable(table, ref size, false, IPv4, OwnerPidListener, 0);
            if (status != 0) throw new Win32Exception((int)status);
            var ports = new Dictionary<int, int>();
            int count = Marshal.ReadInt32(table);
            for (int i = 0; i < count; i++)
            {
                int row = sizeof(int) + i * RowSize;
                int address = Marshal.ReadInt32(table, row + 4);
                if (address != 0 && address != 0x0100007f) continue;
                int port = (ushort)IPAddress.NetworkToHostOrder((short)Marshal.ReadInt32(table, row + 8));
                if (address == 0 && ports.ContainsKey(port)) continue;
                ports[port] = Marshal.ReadInt32(table, row + 20);
            }
            return ports;
        }
        finally { Marshal.FreeHGlobal(table); }
    }
}

sealed class WebLauncher
{
    public const string EventPrefix = @"Local\GXWorksAgent-Web-";
    static readonly string LocalData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
    public static readonly string UserState = Path.Combine(LocalData, "gxworks-agent");
    readonly string root;
    readonly string settingsFile;
    readonly JavaScriptSerializer json = new JavaScriptSerializer();

    public WebLauncher(string root, string settingsFile)
    {
        this.root = root;
        this.settingsFile = settingsFile;
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CommandLineToArgvW(string commandLine, out int count);
    [DllImport("kernel32.dll")]
    static extern IntPtr LocalFree(IntPtr memory);

    static string[] CommandArguments(string commandLine)
    {
        int count;
        IntPtr argv = CommandLineToArgvW(commandLine, out count);
        if (argv == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            var args = new string[count];
            for (int i = 0; i < count; i++) args[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(argv, i * IntPtr.Size));
            return args;
        }
        finally { LocalFree(argv); }
    }

    static string Option(string[] args, string name)
    {
        for (int i = 1; i < args.Length; i++)
        {
            if (args[i] == name && i + 1 < args.Length) return args[i + 1];
            if (args[i].StartsWith(name + "=", StringComparison.Ordinal)) return args[i].Substring(name.Length + 1);
        }
        return null;
    }

    static List<WorkbenchProcess> Discover(Dictionary<int, int> listeners)
    {
        var found = new List<WorkbenchProcess>();
        if (listeners.Count == 0) return found;
        string sid;
        using (var identity = WindowsIdentity.GetCurrent()) sid = identity.User.Value;
        using (var searcher = new ManagementObjectSearcher(
            "SELECT Name, ProcessId, CommandLine FROM Win32_Process WHERE Name='python.exe' OR Name='pythonw.exe' OR Name='GXWorks-Agent-Web.exe'"))
        using (var processes = searcher.Get())
        {
            foreach (ManagementObject process in processes)
            using (process)
            {
                int id = Convert.ToInt32(process["ProcessId"]);
                if (!listeners.ContainsValue(id)) continue;
                string command = process["CommandLine"] as string;
                if (String.IsNullOrEmpty(command)) continue;
                var args = CommandArguments(command);
                string executable = (string)process["Name"];
                int module = Array.IndexOf(args, "-m");
                bool source = module >= 1 && module + 1 < args.Length && args[module + 1] == "integrations.web";
                if (!source && !String.Equals(executable, "GXWorks-Agent-Web.exe", StringComparison.OrdinalIgnoreCase)) continue;
                int port, ownerId;
                if (!Int32.TryParse(Option(args, "--port") ?? "8765", out port) ||
                    !listeners.TryGetValue(port, out ownerId) || ownerId != id) continue;
                string state = Option(args, "--state-dir");
                if (!String.IsNullOrEmpty(state) && !Path.IsPathRooted(state)) continue;
                using (var instance = new ManagementObject("Win32_Process.Handle=\"" + id + "\""))
                using (var owner = instance.InvokeMethod("GetOwnerSid", null, null))
                    if (owner == null || !String.Equals(owner["Sid"] as string, sid, StringComparison.Ordinal)) continue;
                found.Add(new WorkbenchProcess { Id = id, StopEvent = Option(args, "--launcher-event"),
                    Settings = new LaunchSettings { Port = port, StateDirectory = state,
                        ReadOnly = args.Contains("--read-only"), NoServiceDiscovery = args.Contains("--no-service-discovery") } });
            }
        }
        return found;
    }

    Dictionary<string, object> ReadApi(int port, string resource)
    {
        var request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + "/api/" + resource);
        request.Proxy = null;
        request.AllowAutoRedirect = false;
        request.Timeout = request.ReadWriteTimeout = 1500;
        using (var response = (HttpWebResponse)request.GetResponse())
        using (var reader = new StreamReader(response.GetResponseStream()))
        {
            if (response.StatusCode != HttpStatusCode.OK) throw new IOException("无法读取现有工作台状态。");
            return json.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
        }
    }

    bool IsWorkbench(int port)
    {
        try
        {
            object application, status;
            var health = ReadApi(port, "health");
            return health.TryGetValue("application", out application) && Equals(application, "gxworks-agent") &&
                   health.TryGetValue("status", out status) && Equals(status, "ok");
        }
        catch (WebException) { return false; }
        catch (ArgumentException) { return false; }
    }

    public LaunchSettings Load()
    {
        string source = File.Exists(settingsFile) ? settingsFile : Path.Combine(LocalData, "GXWorks Agent", "web-launcher.json");
        if (File.Exists(source))
        {
            try
            {
                var saved = json.Deserialize<LaunchSettings>(File.ReadAllText(source));
                if (saved != null && saved.Port >= 1024 && saved.Port <= 65535) return saved;
            }
            catch (ArgumentException) { }
        }
        var running = Discover(WindowsListeners.LocalPorts());
        if (running.Count == 1 && IsWorkbench(running[0].Settings.Port))
            return running[0].Settings;
        return new LaunchSettings { Port = 8765 };
    }

    static string StatePath(string path)
    {
        string fullPath = Path.GetFullPath(String.IsNullOrWhiteSpace(path) ? UserState : path.Trim());
        return fullPath.Length == Path.GetPathRoot(fullPath).Length ? fullPath : fullPath.TrimEnd('\\', '/');
    }

    void RequireIdle(WorkbenchProcess service)
    {
        int port = service.Settings.Port;
        if (!IsWorkbench(port)) throw new IOException("无法确认端口 " + port + " 上的旧工作台状态。");
        object value;
        if (ReadApi(port, "workspace").TryGetValue("selected", out value) && Equals(value, false)) return;
        if (!ReadApi(port, "jobs").TryGetValue("jobs", out value) || !(value is System.Collections.IList))
            throw new IOException("无法读取旧工作台的任务状态。");
        foreach (var row in (System.Collections.IList)value)
        {
            object status;
            var item = row as Dictionary<string, object>;
            if (item == null || !item.TryGetValue("status", out status) || !(status is string))
                throw new IOException("无法读取旧工作台的任务状态。");
            if (new[] { "queued", "running", "cancelling" }.Contains((string)status))
                throw new IOException("旧工作台仍有任务在运行，请等任务结束后重试。");
        }
    }

    static void Stop(WorkbenchProcess service)
    {
        int owner;
        if (!WindowsListeners.LocalPorts().TryGetValue(service.Settings.Port, out owner)) return;
        if (owner != service.Id) throw new IOException("旧工作台的端口已被另一个进程接管。");
        using (var process = Process.GetProcessById(service.Id))
        {
            if (service.StopEvent != null && service.StopEvent.StartsWith(EventPrefix, StringComparison.Ordinal))
            {
                using (var stop = EventWaitHandle.OpenExisting(service.StopEvent)) stop.Set();
            }
            else process.Kill(); // The old CLI has no cooperative shutdown signal.
            if (!process.WaitForExit(20000)) throw new IOException("旧工作台未能在 20 秒内关闭。");
        }
    }

    static string Quote(string value)
    {
        if (value.Length > 0 && !value.Any(character => Char.IsWhiteSpace(character) || character == '"')) return value;
        return "\"" + value.Replace("\"", "\\\"") + new string('\\', value.Length - value.TrimEnd('\\').Length) + "\"";
    }

    ProcessStartInfo StartInfo(string executable, IEnumerable<string> args)
    {
        return new ProcessStartInfo(executable, String.Join(" ", args.Select(Quote))) {
            UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = root };
    }

    ProcessStartInfo Backend(List<string> args)
    {
        if (!File.Exists(Path.Combine(root, "src", "integrations", "web", "__main__.py")))
        {
            string executable = Path.Combine(root, "GXWorks-Agent-Web.exe");
            if (!File.Exists(executable)) throw new IOException("找不到 GXWorks-Agent-Web.exe，请完整解压发布包。");
            return StartInfo(executable, args);
        }
        string python = Path.Combine(root, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python))
        {
            var locate = StartInfo("py.exe", new[] { "-3.13", "-c", "import sys; print(sys.executable)" });
            locate.RedirectStandardOutput = true;
            using (var finder = Process.Start(locate))
            {
                if (!finder.WaitForExit(8000)) { finder.Kill(); throw new IOException("查找 Python 超时。"); }
                python = finder.StandardOutput.ReadToEnd().Trim();
                if (finder.ExitCode != 0 || !File.Exists(python))
                    throw new IOException("找不到可用的 Python Web 环境。请先双击 build-web.bat。");
            }
        }
        var info = StartInfo(python, new[] { "-m", "integrations.web" }.Concat(args));
        info.EnvironmentVariables["PYTHONPATH"] = Path.Combine(root, "src");
        info.EnvironmentVariables["PYTHONUTF8"] = "1";
        return info;
    }

    public string SavedGxWorks2Directory()
    {
        string configured = Environment.GetEnvironmentVariable("GXWORKS2_EXE");
        if (!String.IsNullOrWhiteSpace(configured))
            return GxWorks2Directory(Environment.ExpandEnvironmentVariables(configured.Trim().Trim('"')));

        // The shared GX Works2 finder owns this cache in the application data directory.
        string data = Environment.GetEnvironmentVariable("PLC_AI_DATA_DIR");
        try
        {
            if (String.IsNullOrWhiteSpace(data))
            {
                string roaming = (Environment.GetEnvironmentVariable("APPDATA") ?? "").Trim();
                if (!Path.IsPathRooted(roaming)) roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                data = Path.Combine(roaming, "gxworks-agent");
            }
            string file = Path.Combine(data.Trim(), "gxworks2-installation.json");
            if (!File.Exists(file)) return null;
            var record = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(file));
            object executable;
            if (record != null && record.TryGetValue("gx_works2_executable", out executable))
                return GxWorks2Directory(executable as string);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        catch (ArgumentException) { }
        catch (InvalidOperationException) { }
        return null;
    }

    static string GxWorks2Directory(string executable)
    {
        if (!File.Exists(executable) || !Path.IsPathRooted(executable) ||
            !String.Equals(Path.GetFileName(executable), "GD2.exe", StringComparison.OrdinalIgnoreCase)) return null;
        return Path.GetDirectoryName(executable);
    }

    public string DetectGxWorks2Directory()
    {
        var info = Backend(new List<string> { "--gxworks2-directory" });
        info.RedirectStandardOutput = true;
        info.StandardOutputEncoding = Encoding.UTF8;
        info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        using (var process = Process.Start(info))
        {
            var output = process.StandardOutput.ReadToEndAsync();
            if (!process.WaitForExit(20000))
            {
                process.Kill();
                throw new IOException("GX Works2 检测超时。");
            }
            if (process.ExitCode != 0) throw new IOException("GX Works2 检测失败。");
            return output.GetAwaiter().GetResult().TrimEnd('\r', '\n');
        }
    }

    public string Restart(LaunchSettings settings)
    {
        if (settings.Port < 1024 || settings.Port > 65535) throw new ArgumentOutOfRangeException("端口");
        if (!File.Exists(Path.Combine(root, "web", "dist", "index.html")))
            throw new IOException("缺少网页文件。源码目录请先双击 build-web.bat，发布包请完整解压。");
        var args = new List<string> { "--port", settings.Port.ToString() };
        string state = StatePath(settings.StateDirectory);
        if (!String.IsNullOrWhiteSpace(settings.StateDirectory))
        {
            settings.StateDirectory = state;
            args.AddRange(new[] { "--state-dir", state });
        }
        if (settings.ReadOnly) args.Add("--read-only");
        if (settings.NoServiceDiscovery) args.Add("--no-service-discovery");
        string stopEvent = EventPrefix + Guid.NewGuid().ToString("N");
        string log = Path.Combine(state, "workbench-launch.log");
        args.AddRange(new[] { "--launcher-event", stopEvent, "--launcher-log", log });
        var start = Backend(args);
        var ports = WindowsListeners.LocalPorts();
        var previous = Discover(ports).Where(p => String.Equals(StatePath(p.Settings.StateDirectory), state, StringComparison.OrdinalIgnoreCase)).ToList();
        int owner;
        if (ports.TryGetValue(settings.Port, out owner) && !previous.Any(p => p.Id == owner))
            throw new IOException("端口 " + settings.Port + " 已被其他程序或另一个工作台占用，请换一个端口。");
        if (!ports.ContainsKey(settings.Port))
        {
            var check = new TcpListener(IPAddress.Loopback, settings.Port) { ExclusiveAddressUse = true };
            try { check.Start(); }
            catch (SocketException) { throw new IOException("无法绑定端口 " + settings.Port + "，请换一个端口。"); }
            finally { check.Stop(); }
        }
        foreach (var service in previous) RequireIdle(service);
        Directory.CreateDirectory(state);
        foreach (var service in previous) Stop(service);

        using (var process = Process.Start(start))
        {
            var deadline = DateTime.UtcNow.AddSeconds(40);
            while (!process.HasExited && DateTime.UtcNow < deadline)
            {
                var listeners = WindowsListeners.LocalPorts();
                // The Windows venv redirector can start a separate interpreter.
                if (listeners.TryGetValue(settings.Port, out owner) &&
                    (owner == process.Id || Discover(listeners).Any(p => p.Id == owner && p.StopEvent == stopEvent)) &&
                    IsWorkbench(settings.Port))
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(settingsFile));
                    File.WriteAllText(settingsFile, json.Serialize(settings));
                    return "http://127.0.0.1:" + settings.Port + "/";
                }
                Thread.Sleep(200);
            }
            if (!process.HasExited) process.Kill();
        }
        throw new IOException("工作台未能启动。日志位于 " + log);
    }
}

static class LauncherWindow
{
    // Match the light workbench tokens in web/src/styles.css.
    static readonly Color Background = Color.FromArgb(0xfa, 0xfa, 0xf9);
    static readonly Color Ink = Color.FromArgb(0x29, 0x29, 0x27);
    static readonly Color Muted = Color.FromArgb(0x5f, 0x5f, 0x5b);
    static readonly Color Border = Color.FromArgb(0xde, 0xde, 0xd9);
    static readonly Color Accent = Color.FromArgb(0x2f, 0x66, 0xb4);
    static readonly Color Primary = Color.FromArgb(0x34, 0x6e, 0xc7);
    static readonly Color Error = Color.FromArgb(0xa6, 0x2c, 0x43);

    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try { ShowWindow(); }
        catch (Exception error) { MessageBox.Show(error.Message, "GXWorks Agent 启动器", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }

    static void ShowWindow()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        var launcher = new WebLauncher(root, Path.Combine(WebLauncher.UserState, "web-launcher.json"));
        var settings = launcher.Load();
        var form = new Form { Text = "GXWorks Agent 启动器", StartPosition = FormStartPosition.CenterScreen,
            AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, MaximizeBox = false,
            FormBorderStyle = FormBorderStyle.FixedSingle, Font = new Font("Segoe UI", 9.5f),
            BackColor = Background, ForeColor = Ink,
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath) };
        form.SuspendLayout();

        const int CardWidth = 360;
        var page = Stack(CardWidth, new Padding(24));
        page.Dock = DockStyle.None;
        form.Controls.Add(page);
        var header = new TableLayoutPanel { ColumnCount = 2, RowCount = 2, AutoSize = true,
            Dock = DockStyle.Top, Margin = new Padding(0, 0, 0, 18) };
        header.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var brand = new PictureBox { Image = form.Icon.ToBitmap(), Size = new Size(36, 36),
            SizeMode = PictureBoxSizeMode.Zoom, Anchor = AnchorStyles.Left, Margin = new Padding(0, 0, 12, 0) };
        header.Controls.Add(brand, 0, 0);
        header.SetRowSpan(brand, 2);
        header.Controls.Add(new Label { Text = "GXWorks Agent", AutoSize = true, Margin = Padding.Empty,
            Font = new Font(form.Font.FontFamily, 15f, FontStyle.Bold) }, 1, 0);
        header.Controls.Add(new Label { Text = "本地 Web 工作台", ForeColor = Muted, AutoSize = true,
            Margin = new Padding(0, 2, 0, 0) }, 1, 1);
        page.Controls.Add(header, 0, 0);

        var card = Stack(CardWidth - 32, new Padding(16));
        card.Paint += (sender, e) => PaintSurface(card, e.Graphics, Border, 12);
        page.Controls.Add(card, 0, 1);
        card.Controls.Add(new Label { Text = "访问端口", AutoSize = true, Margin = new Padding(0, 0, 0, 8),
            BackColor = Color.White, Font = new Font(form.Font, FontStyle.Bold) }, 0, 0);

        var address = new TableLayoutPanel { ColumnCount = 2, RowCount = 1, Height = 38, Dock = DockStyle.Top,
            Padding = new Padding(11, 0, 11, 0), Margin = new Padding(0, 0, 0, 14), BackColor = Color.White };
        address.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        address.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        var mono = new Font("Consolas", 11f);
        address.Controls.Add(new Label { Text = "http://127.0.0.1:", ForeColor = Muted, Font = mono,
            AutoSize = true, Anchor = AnchorStyles.Left, Margin = Padding.Empty }, 0, 0);
        var port = new TextBox { Text = settings.Port.ToString(), MaxLength = 5, BorderStyle = BorderStyle.None,
            AccessibleName = "访问端口", Anchor = AnchorStyles.Left | AnchorStyles.Right,
            BackColor = Color.White, ForeColor = Ink, Font = mono, Margin = Padding.Empty };
        address.Controls.Add(port, 1, 0);
        address.Paint += (sender, e) => PaintSurface(address, e.Graphics, port.Focused ? Accent : Border, 8);
        port.Enter += (sender, e) => address.Invalidate();
        port.Leave += (sender, e) => address.Invalidate();
        card.Controls.Add(address, 0, 1);

        var start = new Button { Text = "启动 / 重启工作台", Height = 38, Dock = DockStyle.Top, Margin = Padding.Empty,
            FlatStyle = FlatStyle.Flat, BackColor = Primary, ForeColor = Color.White, UseVisualStyleBackColor = false,
            Font = new Font(form.Font, FontStyle.Bold) };
        start.FlatAppearance.BorderSize = 0;
        start.FlatAppearance.MouseOverBackColor = Accent;
        start.FlatAppearance.MouseDownBackColor = Accent;
        start.Resize += delegate {
            if (start.Width < 1 || start.Height < 1) return;
            var previous = start.Region;
            using (var shape = RoundedRectangle(start.ClientRectangle, start.Height * (8f / 38f)))
                start.Region = new Region(shape);
            if (previous != null) previous.Dispose();
        };
        card.Controls.Add(start, 0, 2);
        form.AcceptButton = start;
        var status = new LinkLabel { AutoSize = true, MaximumSize = new Size(CardWidth - 32, 0),
            BackColor = Color.White, Margin = new Padding(0, 12, 0, 0), LinkBehavior = LinkBehavior.HoverUnderline,
            LinkColor = Accent, ActiveLinkColor = Accent };
        card.Controls.Add(status, 0, 3);
        Action<string, Color, string> setStatus = (message, color, url) => {
            status.Text = message;
            status.ForeColor = color;
            status.Links.Clear();
            status.TabStop = url != null;
            if (url != null) status.Links.Add(0, message.Length, url);
        };
        setStatus("启动后会自动打开浏览器。", Muted, null);
        status.LinkClicked += (sender, e) => OpenBrowser((string)e.Link.LinkData);

        const string GxDirectoryLabel = "本机 GX Works2 目录：";
        string savedDirectory = launcher.SavedGxWorks2Directory();
        var gxStatus = new Label { Text = savedDirectory == null ? "本机 GX Works2：检测中…" : GxDirectoryLabel + savedDirectory,
            ForeColor = Muted, AutoSize = true,
            MaximumSize = new Size(CardWidth, 0), Margin = new Padding(0, 14, 0, 0) };
        page.Controls.Add(gxStatus, 0, 2);
        if (savedDirectory == null)
            form.Shown += async delegate {
                try
                {
                    string directory = await Task.Run(() => launcher.DetectGxWorks2Directory());
                    gxStatus.Text = directory.Length == 0 ? "本机 GX Works2：未检测到" : GxDirectoryLabel + directory;
                }
                catch (Exception) { gxStatus.Text = "本机 GX Works2：检测失败"; }
            };

        bool starting = false;
        form.FormClosing += delegate(object sender, FormClosingEventArgs e) { if (starting) e.Cancel = true; };
        start.Click += async delegate {
            int selectedPort;
            if (!Int32.TryParse(port.Text.Trim(), out selectedPort) || selectedPort < 1024 || selectedPort > 65535)
            {
                setStatus("请输入 1024 到 65535 之间的端口。", Error, null);
                port.Focus();
                return;
            }
            starting = true;
            start.Enabled = false;
            port.ReadOnly = true;
            setStatus("正在启动工作台…", Muted, null);
            settings.Port = selectedPort;
            try
            {
                string url = await Task.Run(() => launcher.Restart(settings));
                setStatus("已启动 · 在浏览器打开工作台", Accent, url);
                OpenBrowser(url);
            }
            catch (Exception error) { setStatus(error.Message, Error, null); }
            finally { starting = false; port.ReadOnly = false; start.Enabled = true; }
        };
        form.AutoScaleDimensions = new SizeF(96, 96);
        form.AutoScaleMode = AutoScaleMode.Dpi;
        form.ResumeLayout(true);
        Application.Run(form);
    }

    static TableLayoutPanel Stack(int contentWidth, Padding padding)
    {
        var table = new TableLayoutPanel { AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            ColumnCount = 1, Dock = DockStyle.Top, Padding = padding, Margin = Padding.Empty };
        table.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, contentWidth));
        return table;
    }

    static void PaintSurface(Control control, Graphics graphics, Color border, float radius)
    {
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        var bounds = new RectangleF(0.5f, 0.5f, control.Width - 1, control.Height - 1);
        using (var shape = RoundedRectangle(bounds, radius * control.DeviceDpi / 96f))
        using (var pen = new Pen(border))
        {
            graphics.FillPath(Brushes.White, shape);
            graphics.DrawPath(pen, shape);
        }
    }

    static GraphicsPath RoundedRectangle(RectangleF bounds, float radius)
    {
        float corner = Math.Min(2 * radius, Math.Min(bounds.Width, bounds.Height));
        var shape = new GraphicsPath();
        shape.AddArc(bounds.Left, bounds.Top, corner, corner, 180, 90);
        shape.AddArc(bounds.Right - corner, bounds.Top, corner, corner, 270, 90);
        shape.AddArc(bounds.Right - corner, bounds.Bottom - corner, corner, corner, 0, 90);
        shape.AddArc(bounds.Left, bounds.Bottom - corner, corner, corner, 90, 90);
        shape.CloseFigure();
        return shape;
    }

    static void OpenBrowser(string url)
    {
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
        catch (Win32Exception) { MessageBox.Show("请在浏览器打开 " + url, "GXWorks Agent 启动器"); }
    }
}
