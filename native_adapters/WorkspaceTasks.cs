// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

// Original resource/task interfaces, bound to Workspace 1.635.0.1 (x86).
// Python supplies current source, ownership, configuration and append intent.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace PlcAi.NativeAdapter
{
    internal sealed partial class WorkspaceSourceSave
    {
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int TaskCreateCall(IntPtr self, ObjectId parent, out ObjectId child, out int code);

        sealed class TaskRow
        {
            public ObjectId Identity; public string Name; public int Line; public object Facts;
        }
        sealed class TaskInventory
        {
            public ObjectId Resource, Task;
            public string ResourceName, TaskName, EventName, Interval;
            public int Priority;
            public object ResourceFacts, TaskFacts, Configuration;
            public List<TaskRow> Rows;
        }

        object TaskStringSetting(ObjectId task, int offset, string operation, out string value)
        {
            int code; IntPtr text;
            int hr = Slot<IdStringCall>(workspace, offset)(workspace, task, out text, out code);
            try
            {
                Check(operation, hr, code);
                value = ReadBStr(text);
                string field = offset == 1584 ? "event_name" : offset == 1592 ? "interval" : null;
                if (field == null) throw new InvalidOperationException("unobserved_task_setting");
                return new { field = field, hresult = hr, code = code, interface_byte_offset = offset, value = value };
            }
            finally { if (text != IntPtr.Zero) Marshal.FreeBSTR(text); }
        }

        List<TaskInventory> TaskInventories(ObjectId project)
        {
            int code, kind; string name; ObjectId collection;
            Check("Workspace.GetResourceCollection", Slot<CollectionCall>(workspace, 36)(workspace, project, 7, out collection, out code), code);
            object collectionFacts = ObjectFacts(collection, out kind, out name);
            if (kind != 7) throw new InvalidOperationException("native_resource_collection_type_differs");
            Parent(collection, project);
            var result = new List<TaskInventory>();
            foreach (ObjectId resource in Children(collection))
            {
                object resourceFacts = ObjectFacts(resource, out kind, out name);
                if (kind != 8) throw new InvalidOperationException("native_resource_type_differs");
                string resourceName = name; Parent(resource, collection);
                foreach (ObjectId task in Children(resource))
                {
                    object taskFacts = ObjectFacts(task, out kind, out name);
                    if (kind != 10) throw new InvalidOperationException("native_task_type_differs");
                    string taskName = name; Parent(task, resource);
                    string eventName, interval; int priority;
                    object eventFacts = TaskStringSetting(task, 1584, "Workspace.GetTaskEventName", out eventName);
                    object intervalFacts = TaskStringSetting(task, 1592, "Workspace.GetTaskInterval", out interval);
                    int priorityHr = Slot<IdIntegerCall>(workspace, 1600)(workspace, task, out priority, out code);
                    Check("Workspace.GetTaskPriority", priorityHr, code);
                    object configuration = new { event_name = eventFacts, interval = intervalFacts,
                        priority = new { field = "priority", hresult = priorityHr, code = code, interface_byte_offset = 1600, value = priority } };
                    var rows = new List<TaskRow>(); var lines = new HashSet<int>();
                    foreach (ObjectId element in Children(task))
                    {
                        object facts = ObjectFacts(element, out kind, out name);
                        if (kind != 12) throw new InvalidOperationException("native_task_element_type_differs");
                        Parent(element, task); int line;
                        Check("Workspace.GetTaskElementLine", Slot<IdIntegerCall>(workspace, 1624)(workspace, element, out line, out code), code);
                        if (line < 1 || !lines.Add(line)) throw new InvalidOperationException("native_task_line_is_ambiguous");
                        rows.Add(new TaskRow { Identity = element, Name = name, Line = line, Facts = facts });
                    }
                    result.Add(new TaskInventory { Resource = resource, Task = task, ResourceName = resourceName, TaskName = taskName,
                        ResourceFacts = resourceFacts, TaskFacts = taskFacts, EventName = eventName, Interval = interval,
                        Priority = priority, Configuration = configuration, Rows = rows });
                }
            }
            return result;
        }

        static object[] TaskProjection(List<TaskInventory> inventory)
        {
            var result = new List<object>();
            foreach (TaskInventory task in inventory)
            {
                var rows = new List<object>();
                foreach (TaskRow row in task.Rows) rows.Add(new { element = row.Facts, line = row.Line });
                result.Add(new { resource = task.ResourceFacts, task = task.TaskFacts, programs = rows, configuration = task.Configuration });
            }
            return result.ToArray();
        }

        void RemoveEmptyProgram(string root, IDictionary<string, object> request)
        {
            var source = Fields(request["source"], "cpu", "codepage", "operation", "source_name", "before_body", "programs", "tasks");
            PrepareProjectFiles(root); PrepareTemporaryDirectory(Text(request["owner_token"])); Initialize(Text(request["installation"]));
            IntPtr home, ws, project; int code;
            ObjectId id = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out project);
            ScopeVerifySources(id, Entries(source["programs"]));
            var tasks = TaskInventories(id); VerifyTaskSelection(tasks, Entries(source["tasks"]));
            string name = Text(source["source_name"]); object selection;
            ObjectId body = SourceIdentity(id, name, 208, "remove-empty-program", out selection), owner, labels;
            if (!Equal(ReadBody(body), Body(source["before_body"]))) throw new InvalidOperationException("selected_empty_source_differs");
            Check("Workspace.GetPOUOwner", Slot<ParentCall>(workspace, 40)(workspace, body, out owner, out code), code);
            int type; Check("Workspace.GetPOUType", Slot<IdIntegerCall>(workspace, 1668)(workspace, owner, out type, out code), code);
            Check("Workspace.GetLocalLabels", Slot<ParentCall>(workspace, 1748)(workspace, body, out labels, out code), code);
            if (type != 0x01000000 || Children(labels).Count != 0) throw new InvalidOperationException("selected_empty_program_type_or_labels_differ");
            foreach (TaskInventory task in tasks)
                foreach (TaskRow row in task.Rows)
                    if (row.Name == name) Check("Definition.RemoveEmptyProgramTaskRow", Slot<DeleteCall>(workspace, 20)(workspace, row.Identity, out code), code);
            Check("Definition.RemoveEmptyProgram", Slot<DeleteCall>(workspace, 20)(workspace, owner, out code), code);
            var remaining = new List<object>();
            foreach (object row in Entries(source["programs"]))
                if (Text(((IDictionary<string, object>)row)["name"]) != name) remaining.Add(row);
            ScopeVerifySources(id, remaining.ToArray());
            Check("Definition.MarkProjectEdited", Slot<IdSetIntegerCall>(workspace, 68)(workspace, id, 1, out code), code);
            Check("Definition.MarkProjectCompileChanged", Slot<IdSetIntegerCall>(workspace, 1560)(workspace, id, 1, out code), code);
            ExportSavedProject(root, id, home, ws, project);
        }

        static void VerifyTaskSelection(List<TaskInventory> actual, object[] expected)
        {
            if (actual.Count != expected.Length) throw new InvalidOperationException("native_task_membership_differs");
            var selected = new List<TaskInventory>();
            foreach (object item in expected)
            {
                var row = Fields(item, "resource", "task", "event_name", "interval", "priority", "programs");
                var matches = actual.FindAll(delegate(TaskInventory value) {
                    return value.ResourceName == Text(row["resource"]) && value.TaskName == Text(row["task"]);
                });
                if (matches.Count != 1 || selected.Contains(matches[0])) throw new InvalidOperationException("native_task_ownership_differs");
                TaskInventory task = matches[0]; selected.Add(task);
                if (task.EventName != Text(row["event_name"]) || task.Interval != Text(row["interval"])
                        || task.Priority != NativeRequest.Integer(row["priority"]))
                    throw new InvalidOperationException("native_task_configuration_differs");
                object[] programs = Entries(row["programs"]);
                if (programs.Length != task.Rows.Count) throw new InvalidOperationException("native_task_program_membership_differs");
                var selectedRows = new List<TaskRow>();
                foreach (object value in programs)
                {
                    var program = Fields(value, "name", "line");
                    var rows = task.Rows.FindAll(delegate(TaskRow entry) {
                        return entry.Name == Text(program["name"]) && entry.Line == NativeRequest.Integer(program["line"]);
                    });
                    if (rows.Count != 1 || selectedRows.Contains(rows[0])) throw new InvalidOperationException("native_task_program_order_differs");
                    selectedRows.Add(rows[0]);
                }
            }
        }

        sealed class ResourceRow
        {
            public ObjectId Identity; public string Name; public object Facts;
        }

        List<ResourceRow> ResourceInventory(ObjectId project, out ObjectId collection, out object collectionFacts)
        {
            int code, kind; string name;
            Check("Workspace.GetResourceCollection", Slot<CollectionCall>(workspace, 36)(workspace, project, 7, out collection, out code), code);
            collectionFacts = ObjectFacts(collection, out kind, out name);
            if (kind != 7) throw new InvalidOperationException("native_resource_collection_type_differs");
            Parent(collection, project); var result = new List<ResourceRow>();
            foreach (ObjectId resource in Children(collection))
            {
                object facts = ObjectFacts(resource, out kind, out name);
                if (kind != 8) throw new InvalidOperationException("native_resource_type_differs");
                Parent(resource, collection);
                result.Add(new ResourceRow { Identity = resource, Name = name, Facts = facts });
            }
            return result;
        }

        static object[] ResourceProjection(List<ResourceRow> resources)
        {
            var result = new List<object>(); foreach (ResourceRow row in resources) result.Add(row.Facts);
            return result.ToArray();
        }

        void ProjectPouParent(ObjectId owner, ObjectId project, ObjectId collection, int pouType)
        {
            int code; ObjectId parent;
            Check("Workspace.GetPOUParent", Slot<ParentCall>(workspace, 40)(workspace, owner, out parent, out code), code);
            if (Same(parent, collection)) return;
            // Simple+labels uses separate Program and FB collections while
            // GetPOUCollection exposes their combined membership. Verify the
            // actual collection and its project instead of discarding it.
            int expectedType = pouType == 0x1000000 ? 0x1000000 : pouType == 0x1000002 ? 0x1000001 : -1;
            if (expectedType < 0) throw new InvalidOperationException("native_pou_parent_kind_uncovered:" + pouType);
            Facts(parent, expectedType, ""); Parent(parent, project);
            if (!Children(parent).Exists(delegate(ObjectId child) { return Same(child, owner); }))
                throw new InvalidOperationException("native_pou_parent_membership_differs");
        }

        object[] ScopeVerifySources(ObjectId project, object[] expected)
        {
            int code; ObjectId collection;
            Check("Workspace.GetPOUCollection", Slot<CollectionCall>(workspace, 36)(workspace, project, 25, out collection, out code), code);
            Parent(collection, project);
            var owners = Children(collection); var verified = new List<ObjectId>(); var result = new List<object>();
            foreach (object item in expected)
            {
                var row = Fields(item, "name", "program_kind", "pou_type", "body"); object selection; ObjectId owner;
                ObjectId body = SourceIdentity(project, Text(row["name"]), NativeRequest.Integer(row["program_kind"]), "scope_creation", out selection);
                Check("Workspace.GetPOUOwner", Slot<ParentCall>(workspace, 40)(workspace, body, out owner, out code), code);
                int type;
                Check("Workspace.GetPOUType", Slot<IdIntegerCall>(workspace, 1668)(workspace, owner, out type, out code), code);
                ProjectPouParent(owner, project, collection, type);
                if (type != NativeRequest.Integer(row["pou_type"]) || !Equal(ReadBody(body), Body(row["body"]))
                        || !owners.Exists(delegate(ObjectId value) { return Same(value, owner); })
                        || verified.Exists(delegate(ObjectId value) { return Same(value, owner); }))
                    throw new InvalidOperationException("native_scope_current_source_differs");
                verified.Add(owner); result.Add(new { source = selection, pou_type = type });
            }
            if (owners.Count != verified.Count) throw new InvalidOperationException("native_scope_source_membership_differs");
            return result.ToArray();
        }

        static void VerifyOldTasks(List<TaskInventory> before, List<TaskInventory> after)
        {
            foreach (TaskInventory old in before)
            {
                var matches = after.FindAll(delegate(TaskInventory row) {
                    return Same(row.Resource, old.Resource) && Same(row.Task, old.Task)
                        && row.ResourceName == old.ResourceName && row.TaskName == old.TaskName;
                });
                if (matches.Count != 1) throw new InvalidOperationException("native_scope_existing_task_identity_changed");
                TaskInventory current = matches[0];
                if (current.EventName != old.EventName || current.Interval != old.Interval || current.Priority != old.Priority
                        || current.Rows.Count != old.Rows.Count)
                    throw new InvalidOperationException("native_scope_existing_task_configuration_or_rows_changed");
                foreach (TaskRow row in old.Rows)
                    if (current.Rows.FindAll(delegate(TaskRow value) {
                        return Same(value.Identity, row.Identity) && value.Name == row.Name && value.Line == row.Line;
                    }).Count != 1) throw new InvalidOperationException("native_scope_existing_task_row_changed");
            }
        }

        void CreateScope(string root, IDictionary<string, object> request, bool taskScope)
        {
            NativeRequest.Version(request, 1);
            var source = taskScope
                ? Fields(request["source"], "cpu", "codepage", "resource", "task", "programs", "resources", "tasks")
                : Fields(request["source"], "cpu", "codepage", "resource", "programs", "resources", "tasks");
            string resourceName = Text(source["resource"]), name = taskScope ? Text(source["task"]) : resourceName;
            if (String.IsNullOrEmpty(name)) throw new ArgumentException("native_scope_name_empty");
            PrepareProjectFiles(root); PrepareTemporaryDirectory(Text(request["owner_token"])); Initialize(Text(request["installation"]));
            IntPtr home, ws, project;
            ObjectId projectId = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out project);
            object[] programs = Entries(source["programs"]), beforeSources = ScopeVerifySources(projectId, programs);
            ObjectId collection; object collectionFacts;
            List<ResourceRow> beforeResources = ResourceInventory(projectId, out collection, out collectionFacts);
            object[] expectedResources = Entries(source["resources"]);
            if (beforeResources.Count != expectedResources.Length) throw new InvalidOperationException("native_scope_resource_membership_differs");
            var selectedResources = new List<ResourceRow>();
            foreach (object expected in expectedResources)
            {
                var matches = beforeResources.FindAll(delegate(ResourceRow row) { return row.Name == Text(expected); });
                if (matches.Count != 1 || selectedResources.Contains(matches[0])) throw new InvalidOperationException("native_scope_resource_name_differs");
                selectedResources.Add(matches[0]);
            }
            List<TaskInventory> beforeTasks = TaskInventories(projectId);
            VerifyTaskSelection(beforeTasks, Entries(source["tasks"]));
            ResourceRow selectedResource = null;
            ObjectId parent = collection; object parentFacts = collectionFacts;
            if (taskScope)
            {
                var matches = beforeResources.FindAll(delegate(ResourceRow row) { return row.Name == resourceName; });
                if (matches.Count != 1 || beforeTasks.Exists(delegate(TaskInventory row) { return String.Equals(row.TaskName, name, StringComparison.OrdinalIgnoreCase); }))
                    throw new InvalidOperationException("native_task_scope_parent_or_name_differs");
                selectedResource = matches[0]; int code, kind; string actual;
                Check("Workspace.GetTaskCollection", Slot<CollectionCall>(workspace, 36)(workspace, selectedResource.Identity, 9, out parent, out code), code);
                parentFacts = ObjectFacts(parent, out kind, out actual);
                if (kind != 9) throw new InvalidOperationException("native_task_collection_type_differs");
                // The virtual task collection has no supported GetParent on
                // this interface. Verify its membership against the resource.
                var virtualChildren = Children(parent); var realChildren = Children(selectedResource.Identity);
                if (virtualChildren.Count != realChildren.Count || virtualChildren.Exists(delegate(ObjectId value) { return !realChildren.Exists(delegate(ObjectId other) { return Same(value, other); }); }))
                    throw new InvalidOperationException("native_task_collection_membership_differs");
            }
            else if (beforeResources.Exists(delegate(ResourceRow row) { return String.Equals(row.Name, name, StringComparison.OrdinalIgnoreCase); }))
                throw new InvalidOperationException("native_resource_scope_name_exists");
            string operation = taskScope ? "create_task_scope" : "create_resource_scope";
            CreatedSource = new { operation = operation, stage = "selected_collection", collection = parentFacts,
                before_resources = ResourceProjection(beforeResources), before_tasks = TaskProjection(beforeTasks), existing_sources = beforeSources };
            ObjectId created; int createCode, createdKind; string createdName;
            Check(taskScope ? "Workspace.CreateTaskScopeData" : "Workspace.CreateResourceScopeData",
                Slot<CreateSourceCall>(workspace, 1972)(workspace, parent, BStr(name), taskScope ? 10 : 8, (byte)0, out created, out createCode), createCode);
            if (taskScope)
                Check("Workspace.SetTaskScopeName", Slot<IdSetStringCall>(workspace, 60)(workspace, created, BStr(name), out createCode), createCode);
            object createdFacts = ObjectFacts(created, out createdKind, out createdName);
            if (createdKind != (taskScope ? 10 : 8) || createdName != name) throw new InvalidOperationException("native_created_scope_type_or_name_differs");
            Parent(created, taskScope ? selectedResource.Identity : collection);
            if (Children(created).Count != 0) throw new InvalidOperationException("native_created_scope_is_not_empty");
            ObjectId afterCollection; object afterCollectionFacts;
            List<ResourceRow> afterResources = ResourceInventory(projectId, out afterCollection, out afterCollectionFacts);
            if (!Same(collection, afterCollection) || afterResources.Count != beforeResources.Count + (taskScope ? 0 : 1))
                throw new InvalidOperationException("native_scope_resource_inventory_changed");
            foreach (ResourceRow old in beforeResources)
                if (afterResources.FindAll(delegate(ResourceRow row) { return Same(row.Identity, old.Identity) && row.Name == old.Name; }).Count != 1)
                    throw new InvalidOperationException("native_scope_existing_resource_changed");
            if (!taskScope && afterResources.FindAll(delegate(ResourceRow row) { return Same(row.Identity, created) && row.Name == name; }).Count != 1)
                throw new InvalidOperationException("native_created_resource_differs");
            List<TaskInventory> afterTasks = TaskInventories(projectId);
            if (afterTasks.Count != beforeTasks.Count + (taskScope ? 1 : 0)) throw new InvalidOperationException("native_scope_task_inventory_changed");
            VerifyOldTasks(beforeTasks, afterTasks);
            if (taskScope && afterTasks.FindAll(delegate(TaskInventory row) {
                return Same(row.Resource, selectedResource.Identity) && Same(row.Task, created) && row.TaskName == name && row.Rows.Count == 0;
            }).Count != 1) throw new InvalidOperationException("native_created_task_differs");
            object[] finalSources = ScopeVerifySources(projectId, programs);
            CreatedSource = new { operation = operation, stage = taskScope ? "saved_task" : "saved_resource", collection = parentFacts,
                resource = taskScope ? selectedResource.Facts : createdFacts, created = createdFacts,
                before_resources = ResourceProjection(beforeResources), after_resources = ResourceProjection(afterResources),
                before_tasks = TaskProjection(beforeTasks), after_tasks = TaskProjection(afterTasks),
                existing_sources = beforeSources, final_sources = finalSources };
            ExportSavedProject(root, projectId, home, ws, project);
        }

        void BindProgramTask(string root, IDictionary<string, object> request)
        {
            NativeRequest.Version(request, 1);
            var source = Fields(request["source"], "cpu", "codepage", "program_name", "program_kind", "before_body",
                "resource", "task", "existing_programs", "programs", "tasks");
            string requestedName = Text(source["program_name"]), resourceName = Text(source["resource"]), taskName = Text(source["task"]);
            PrepareProjectFiles(root); PrepareTemporaryDirectory(Text(request["owner_token"])); Initialize(Text(request["installation"]));
            IntPtr home, ws, project;
            ObjectId projectId = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out project);
            int code, kind; string actual; ObjectId collection;
            Check("Workspace.GetPOUCollection", Slot<CollectionCall>(workspace, 36)(workspace, projectId, 25, out collection, out code), code);
            Parent(collection, projectId);
            object[] programs = Entries(source["programs"]);
            var owners = Children(collection); var verified = new List<ObjectId>(); var selections = new List<object>();
            foreach (object item in programs)
            {
                var row = Fields(item, "name", "program_kind", "pou_type", "body"); object selection; ObjectId owner;
                ObjectId body = SourceIdentity(projectId, Text(row["name"]), NativeRequest.Integer(row["program_kind"]), "before_task_binding", out selection);
                Check("Workspace.GetPOUOwner", Slot<ParentCall>(workspace, 40)(workspace, body, out owner, out code), code);
                int type;
                Check("Workspace.GetPOUType", Slot<IdIntegerCall>(workspace, 1668)(workspace, owner, out type, out code), code);
                ProjectPouParent(owner, projectId, collection, type);
                if (type != NativeRequest.Integer(row["pou_type"]) || !Equal(ReadBody(body), Body(row["body"]))
                        || !owners.Exists(delegate(ObjectId value) { return Same(value, owner); })
                        || verified.Exists(delegate(ObjectId value) { return Same(value, owner); }))
                    throw new InvalidOperationException("current_pou_inventory_differs");
                verified.Add(owner); selections.Add(new { source = selection, pou_type = type });
            }
            if (owners.Count != verified.Count) throw new InvalidOperationException("current_pou_membership_differs");
            object requestedSelection; ObjectId requestedOwner;
            ObjectId requestedBody = SourceIdentity(projectId, requestedName, NativeRequest.Integer(source["program_kind"]), "task_binding_target", out requestedSelection);
            Check("Workspace.GetProgramOwner", Slot<ParentCall>(workspace, 40)(workspace, requestedBody, out requestedOwner, out code), code);
            int requestedType;
            Check("Workspace.GetProgramPOUType", Slot<IdIntegerCall>(workspace, 1668)(workspace, requestedOwner, out requestedType, out code), code);
            if (requestedType != 0x01000000 || !Equal(ReadBody(requestedBody), Body(source["before_body"])))
                throw new InvalidOperationException("task_target_is_not_current_program");
            List<TaskInventory> before = TaskInventories(projectId);
            VerifyTaskSelection(before, Entries(source["tasks"]));
            var targets = before.FindAll(delegate(TaskInventory value) { return value.ResourceName == resourceName && value.TaskName == taskName; });
            if (targets.Count != 1) throw new InvalidOperationException("task_target_not_unique");
            TaskInventory selected = targets[0]; object[] expected = Entries(source["existing_programs"]);
            var ordered = new List<TaskRow>(selected.Rows); ordered.Sort(delegate(TaskRow a, TaskRow b) { return a.Line.CompareTo(b.Line); });
            if (ordered.Count != expected.Length) throw new InvalidOperationException("selected_task_membership_differs");
            for (int i = 0; i < ordered.Count; i++)
                if (ordered[i].Name != Text(expected[i])) throw new InvalidOperationException("selected_task_order_differs");
            foreach (TaskInventory task in before)
                foreach (TaskRow row in task.Rows)
                    if (String.Equals(row.Name, requestedName, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException("program_already_registered");
            int last = 0; foreach (TaskRow row in selected.Rows) last = Math.Max(last, row.Line);
            if (last == Int32.MaxValue) throw new InvalidOperationException("native_task_line_overflow");
            ObjectId created;
            Check("Workspace.CreateTaskElement", Slot<TaskCreateCall>(workspace, 1976)(workspace, selected.Task, out created, out code), code);
            Check("Workspace.SetTaskElementName", Slot<IdSetStringCall>(workspace, 60)(workspace, created, BStr(requestedName), out code), code);
            int line = last + 1;
            Check("Workspace.SetTaskElementLine", Slot<IdSetIntegerCall>(workspace, 1628)(workspace, created, line, out code), code);
            Facts(created, 12, requestedName); Parent(created, selected.Task);
            List<TaskInventory> after = TaskInventories(projectId);
            if (after.Count != before.Count) throw new InvalidOperationException("task_inventory_changed");
            foreach (TaskInventory old in before)
            {
                var matches = after.FindAll(delegate(TaskInventory value) {
                    return Same(value.Resource, old.Resource) && Same(value.Task, old.Task)
                        && value.ResourceName == old.ResourceName && value.TaskName == old.TaskName;
                });
                if (matches.Count != 1) throw new InvalidOperationException("existing_task_identity_changed");
                TaskInventory current = matches[0];
                if (current.EventName != old.EventName || current.Interval != old.Interval || current.Priority != old.Priority
                        || current.Rows.Count != old.Rows.Count + (Same(old.Task, selected.Task) ? 1 : 0))
                    throw new InvalidOperationException("existing_task_configuration_or_membership_changed");
                foreach (TaskRow row in old.Rows)
                    if (current.Rows.FindAll(delegate(TaskRow value) { return Same(value.Identity, row.Identity) && value.Name == row.Name && value.Line == row.Line; }).Count != 1)
                        throw new InvalidOperationException("existing_task_row_changed");
                if (Same(old.Task, selected.Task) && current.Rows.FindAll(delegate(TaskRow value) {
                    return Same(value.Identity, created) && value.Name == requestedName && value.Line == line;
                }).Count != 1) throw new InvalidOperationException("new_task_row_differs");
            }
            var finalOwners = Children(collection);
            if (finalOwners.Count != owners.Count || owners.Exists(delegate(ObjectId owner) { return !finalOwners.Exists(delegate(ObjectId value) { return Same(value, owner); }); }))
                throw new InvalidOperationException("pou_membership_changed_by_binding");
            foreach (object item in programs)
            {
                var row = Fields(item, "name", "program_kind", "pou_type", "body"); object selection;
                ObjectId body = SourceIdentity(projectId, Text(row["name"]), NativeRequest.Integer(row["program_kind"]), "after_task_binding", out selection);
                if (!Equal(ReadBody(body), Body(row["body"]))) throw new InvalidOperationException("source_changed_by_binding");
            }
            object elementFacts = ObjectFacts(created, out kind, out actual);
            CreatedSource = new { operation = "bind_program_task", source = requestedSelection, pou_type = requestedType,
                resource = selected.ResourceFacts, task = selected.TaskFacts, element = elementFacts, line = line,
                before_tasks = TaskProjection(before), after_tasks = TaskProjection(after), existing_sources = selections };
            ExportSavedProject(root, projectId, home, ws, project);
        }
    }
}
