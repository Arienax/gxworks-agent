using System;
using System.Diagnostics;
using System.Globalization;
using System.IO.MemoryMappedFiles;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace PlcAi.NativeAdapter
{
    // The selection writer never owns an unmonitored SuspendThread increment.
    // Windows 11 binds it to a kernel object. Older Windows uses a separate
    // guardian which owns suspension and observes the writer's process handle.
    internal sealed class NativeUiLease : IDisposable
    {
        [StructLayout(LayoutKind.Sequential)] struct FileTime { public uint Low, High; }
        [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct StartupInfo
        {
            public uint Size; public string Reserved, Desktop, Title;
            public uint X, Y, XSize, YSize, XChars, YChars, Fill, Flags;
            public ushort Show, ReservedSize; public IntPtr ReservedBytes, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr Process, Thread; public uint ProcessId, ThreadId; }
        [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
        [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool member);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length, out uint returned);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool CreateProcess(
            string application, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes,
            bool inherit, uint flags, IntPtr environment, string directory, ref StartupInfo startup, out ProcessInfo process);
        [DllImport("kernel32.dll")] static extern uint GetProcessIdOfThread(IntPtr thread);
        [DllImport("kernel32.dll")] static extern bool GetProcessTimes(IntPtr process, out FileTime created, out FileTime exited, out FileTime kernel, out FileTime user);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr source, IntPtr targetProcess, out IntPtr target, uint access, bool inherit, uint options);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError=true)] static extern uint SuspendThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll")] static extern uint WaitForMultipleObjects(uint count, IntPtr[] handles, bool all, uint milliseconds);
        [DllImport("ntdll.dll")] static extern int NtCreateThreadStateChange(out IntPtr state, uint access, IntPtr attributes, IntPtr thread, uint reserved);
        [DllImport("ntdll.dll")] static extern int NtChangeThreadState(IntPtr state, IntPtr thread, int change, IntPtr info, UIntPtr length, uint reserved);
        [DllImport("ntdll.dll")] static extern int NtQueryInformationThread(IntPtr thread, int infoClass, out uint value, uint size, IntPtr returned);

        readonly IntPtr thread;
        IntPtr state;
        Timer deadline;
        Process guardian;
        MemoryMappedFile shared;
        MemoryMappedViewAccessor phase;
        EventWaitHandle ready, release;
        readonly object timerGuard = new object();
        bool disposed;
        internal bool Balanced { get; private set; }
        internal string Mechanism { get; private set; }
        internal int GuardianId { get { return guardian == null ? 0 : guardian.Id; } }

        static void Require(bool value, string reason) { if (!value) throw new InvalidOperationException(reason); }
        static uint SuspendCount(IntPtr thread)
        {
            uint value;
            Require(NtQueryInformationThread(thread, 35, out value, 4, IntPtr.Zero) >= 0, "ui_suspend_count_unavailable");
            return value;
        }
        static string Started(IntPtr process)
        {
            FileTime created, exited, kernel, user;
            Require(GetProcessTimes(process, out created, out exited, out kernel, out user), "selection_owner_identity_unavailable");
            return (((ulong)created.High << 32) | created.Low).ToString(CultureInfo.InvariantCulture);
        }

        internal NativeUiLease(IntPtr thread, uint processId, bool forceLegacy = false, int timeoutMilliseconds = 5000)
        {
            this.thread = thread;
            Require(timeoutMilliseconds > 0 && timeoutMilliseconds <= 5000, "invalid_selection_deadline");
            Require(GetProcessIdOfThread(thread) == processId, "ui_thread_owner_changed");
            Require(SuspendCount(thread) == 0, "owned_ui_was_already_suspended");
            try
            {
                bool native = !forceLegacy;
                if (native)
                {
                    try
                    {
                        int hr = NtCreateThreadStateChange(out state, 1, IntPtr.Zero, thread, 0);
                        Require(hr >= 0 && state != IntPtr.Zero, "create_owned_ui_state_failed");
                    }
                    catch (EntryPointNotFoundException) { native = false; }
                }
                if (native)
                {
                    Mechanism = "kernel_state_handle";
                    // A hung writer is terminated, never resumed underneath a
                    // live WriteProcessMemory call. Kernel handle teardown
                    // releases this lease even when finally cannot execute.
                    deadline = new Timer(delegate {
                        lock (timerGuard) { if (!disposed) Process.GetCurrentProcess().Kill(); }
                    }, null, timeoutMilliseconds, Timeout.Infinite);
                    Require(NtChangeThreadState(state, thread, 0, IntPtr.Zero, UIntPtr.Zero, 0) >= 0,
                            "suspend_owned_ui_state_failed");
                }
                else
                {
                    Mechanism = "independent_guardian";
                    StartGuardian(processId, timeoutMilliseconds);
                }
                Require(SuspendCount(thread) == 1, "owned_ui_suspend_not_exclusive");
            }
            catch { Dispose(); throw; }
        }

        static bool SameImmediateJob(uint processId)
        {
            uint currentId = (uint)Process.GetCurrentProcess().Id;
            for (int capacity = 256; capacity <= 65536; capacity *= 4)
            {
                uint size = (uint)(8 + capacity * IntPtr.Size), returned;
                IntPtr buffer = Marshal.AllocHGlobal((int)size);
                try
                {
                    if (!QueryInformationJobObject(IntPtr.Zero, 3, buffer, size, out returned))
                    {
                        if (Marshal.GetLastWin32Error() == 234) continue;
                        return false;
                    }
                    int count = Marshal.ReadInt32(buffer, 4);
                    if (count < 0 || count > capacity) return false;
                    bool hasCurrent = false, hasTarget = false;
                    for (int i = 0; i < count; i++)
                    {
                        uint pid = unchecked((uint)Marshal.ReadIntPtr(buffer, 8 + i * IntPtr.Size).ToInt64());
                        if (pid == currentId) hasCurrent = true;
                        if (pid == processId) hasTarget = true;
                    }
                    return hasCurrent && hasTarget;
                }
                finally { Marshal.FreeHGlobal(buffer); }
            }
            return false;
        }

        void StartGuardian(uint processId, int timeoutMilliseconds)
        {
            string token = "Local\\gxw-selection-" + Guid.NewGuid().ToString("N");
            shared = MemoryMappedFile.CreateNew(token, 16);
            phase = shared.CreateViewAccessor();
            ready = new EventWaitHandle(false, EventResetMode.ManualReset, token + "-ready");
            release = new EventWaitHandle(false, EventResetMode.ManualReset, token + "-release");
            string arguments = "--selection-guardian " + Process.GetCurrentProcess().Id + " " + Started(GetCurrentProcess()) + " " +
                thread.ToInt64().ToString(CultureInfo.InvariantCulture) + " " + processId + " " + token + " " + timeoutMilliseconds;
            var start = new ProcessStartInfo(Assembly.GetExecutingAssembly().Location, arguments) {
                UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden };
            bool inJob;
            Require(IsProcessInJob(GetCurrentProcess(), IntPtr.Zero, out inJob), "selection_owner_job_unknown");
            if (inJob)
            {
                // Leave the writer's job when the host permits breakaway.
                // Windows can retain an outer host job after this succeeds;
                // membership alone does not mean the writer's job was kept.
                // When breakaway is denied, only allow fallback if the target
                // already shares the writer's immediate job.
                var startup = new StartupInfo { Size = (uint)Marshal.SizeOf(typeof(StartupInfo)) };
                ProcessInfo created;
                if (CreateProcess(start.FileName, new StringBuilder("\"" + start.FileName + "\" " + arguments),
                    IntPtr.Zero, IntPtr.Zero, false, 0x09000000, IntPtr.Zero, null, ref startup, out created))
                {
                    try { guardian = Process.GetProcessById((int)created.ProcessId); }
                    finally { CloseHandle(created.Thread); CloseHandle(created.Process); }
                }
                else
                {
                    Require(SameImmediateJob(processId), "selection_guardian_job_breakaway_denied");
                    guardian = Process.Start(start);
                }
            }
            else guardian = Process.Start(start);
            Require(ready.WaitOne(3000) && phase.ReadInt32(0) == 2 && !guardian.HasExited,
                    "selection_guardian_not_ready");
        }

        internal void Check()
        {
            Require(!disposed, "selection_lease_ended");
            if (guardian != null) Require(!guardian.HasExited && phase.ReadInt32(0) == 2, "selection_guardian_exited");
        }

        public void Dispose()
        {
            lock (timerGuard)
            {
                if (disposed) return;
                disposed = true;
                if (deadline != null) deadline.Dispose();
            }
            try
            {
                if (state != IntPtr.Zero)
                {
                    IntPtr owned = state; state = IntPtr.Zero;
                    Require(CloseHandle(owned), "close_owned_ui_state_failed");
                }
                if (guardian != null)
                {
                    // This method is called only after the writer has left its
                    // critical section. A dead guardian can therefore be
                    // reconciled without resuming beneath an active write.
                    release.Set();
                    if (!guardian.WaitForExit(2000)) { guardian.Kill(); guardian.WaitForExit(); }
                    int progress = phase.ReadInt32(0);
                    if (progress >= 1 && progress <= 3)
                    {
                        uint count = SuspendCount(thread);
                        Require(count <= 1, "guardian_recovery_suspend_count_ambiguous");
                        if (count == 1) Require(ResumeThread(thread) == 1, "guardian_recovery_resume_failed");
                    }
                }
                Balanced = SuspendCount(thread) == 0;
                Require(Balanced, "owned_ui_suspend_balance_not_confirmed");
            }
            finally
            {
                if (guardian != null) guardian.Dispose();
                if (phase != null) phase.Dispose();
                if (shared != null) shared.Dispose();
                if (ready != null) ready.Dispose();
                if (release != null) release.Dispose();
            }
        }

        internal static int RunGuardian(string[] args)
        {
            if (args.Length != 7 || args[0] != "--selection-guardian") return 2;
            int ownerId = Int32.Parse(args[1], CultureInfo.InvariantCulture), timeout = Int32.Parse(args[6], CultureInfo.InvariantCulture);
            uint targetId = UInt32.Parse(args[4], CultureInfo.InvariantCulture);
            if (timeout <= 0 || timeout > 5000 || !System.Text.RegularExpressions.Regex.IsMatch(args[5], @"^Local\\gxw-selection-[a-f0-9]{32}$")) return 2;
            IntPtr owner = IntPtr.Zero, ui = IntPtr.Zero;
            using (var shared = MemoryMappedFile.OpenExisting(args[5]))
            using (var phase = shared.CreateViewAccessor())
            using (var ready = EventWaitHandle.OpenExisting(args[5] + "-ready"))
            using (var release = EventWaitHandle.OpenExisting(args[5] + "-release"))
            {
                bool suspended = false, safeToResume = true;
                try
                {
                    owner = OpenProcess(0x101041, false, ownerId); // synchronize, query, duplicate handle, terminate
                    Require(owner != IntPtr.Zero && Started(owner) == args[2], "selection_owner_changed");
                    Require(DuplicateHandle(owner, new IntPtr(Int64.Parse(args[3], CultureInfo.InvariantCulture)),
                        GetCurrentProcess(), out ui, 0, false, 2), "selection_thread_duplicate_failed");
                    Require(GetProcessIdOfThread(ui) == targetId && SuspendCount(ui) == 0, "selection_thread_not_ready");
                    phase.Write(0, 1); // recovery intent precedes the suspend syscall
                    uint previous = SuspendThread(ui);
                    Require(previous != 0xffffffff, "guardian_suspend_failed");
                    suspended = true;
                    Require(previous == 0, "owned_ui_was_already_suspended");
                    phase.Write(0, 2); safeToResume = false; ready.Set();
                    uint signaled = WaitForMultipleObjects(2, new[] { owner, release.SafeWaitHandle.DangerousGetHandle() }, false, (uint)timeout);
                    safeToResume = signaled == 0 || signaled == 1;
                    if (signaled != 0 && signaled != 1)
                    {
                        // Stop the writer before removing the suspension. No
                        // native operation is replayed to recover its result.
                        Require(TerminateProcess(owner, 124) || WaitForSingleObject(owner, 0) == 0, "selection_writer_stop_failed");
                        Require(WaitForSingleObject(owner, 5000) == 0, "selection_writer_exit_unconfirmed");
                        safeToResume = true;
                    }
                    return 0;
                }
                catch { return 1; }
                finally
                {
                    if (suspended && (safeToResume || WaitForSingleObject(owner, 0) == 0))
                    {
                        phase.Write(0, 3);
                        uint previous = ResumeThread(ui);
                        if (previous != 0xffffffff && previous > 0) phase.Write(0, 4);
                    }
                    else if (!suspended) phase.Write(0, -1);
                    ready.Set();
                    if (ui != IntPtr.Zero) CloseHandle(ui);
                    if (owner != IntPtr.Zero) CloseHandle(owner);
                }
            }
        }
    }
}
