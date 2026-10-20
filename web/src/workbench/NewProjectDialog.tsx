import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import type { Project } from "../api/client";
import { api } from "../api/client";
import { Button, Modal } from "../components/ui";
import type { CreationCapabilities, CreationTarget } from "./useCapabilities";

type Props = {
  open: boolean;
  onClose: () => void;
  creation: CreationCapabilities | null;
  t: (value: string) => string;
  busy: boolean;
  guarded: (action: () => Promise<void>) => Promise<void>;
  onCreated: (project: Project) => void;
};
export function NewProjectDialog({ open, onClose, creation, t, busy, guarded, onCreated }: Props) {
  const [newName, setNewName] = useState("");
  const [newMode, setNewMode] = useState("");
  const [newFamily, setNewFamily] = useState("");
  const [newCPU, setNewCPU] = useState("");
  const [newConcreteCPU, setNewConcreteCPU] = useState("");
  const [newProjectType, setNewProjectType] = useState("");
  const [newLabels, setNewLabels] = useState(false);
  const creationTarget = creation?.native_targets.find((row) => row.menu_cpu === newCPU);
  const creationType = creationTarget?.project_types.find((row) => row.value === newProjectType);
  const labelsRequired = !!creationType?.target_modes.find((mode) => mode.value === newMode)
    ?.labels_required;
  function selectCreationTarget(target: CreationTarget | undefined) {
    setNewCPU(target?.menu_cpu || "");
    setNewConcreteCPU(target?.default_plc_model || "");
    const type = target?.project_types[0];
    setNewProjectType(type?.value || "");
    setNewMode(type?.target_modes[0]?.value || "");
    setNewLabels(!!type?.labels_required);
  }

  useEffect(() => {
    const target = creation?.native_targets.find((row) => row.members.includes(creation.plc_model));
    setNewFamily(target?.family || "");
    selectCreationTarget(target);
  }, [creation]);
  return (
    <Modal open={open} onOpenChange={(v) => !v && onClose()} title={t("新建工程")}>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          void guarded(async () => {
            const p = await api<Project>("/projects", "POST", {
              name: newName,
              target_mode: newMode || "ladder",
              plc_model: newConcreteCPU,
              native_project_type: newProjectType,
              use_labels: labelsRequired || (creationType?.labels_supported !== false && newLabels),
            });
            onCreated(p);
            setNewName("");
          });
        }}
      >
        <label>
          {t("项目名称")}
          <input autoFocus required value={newName} onChange={(e) => setNewName(e.target.value)} />
        </label>
        <label>
          {t("PLC 系列")}
          <select
            aria-label={t("PLC 系列")}
            value={newFamily}
            onChange={(e) => {
              setNewFamily(e.target.value);
              selectCreationTarget(
                creation?.native_targets.find((row) => row.family === e.target.value),
              );
            }}
          >
            {[...new Set(creation?.native_targets.map((row) => row.family) || [])].map((family) => (
              <option key={family} value={family}>
                {family}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("CPU 型号")}
          <select
            aria-label={t("CPU 型号")}
            value={newCPU}
            onChange={(e) =>
              selectCreationTarget(
                creation?.native_targets.find((row) => row.menu_cpu === e.target.value),
              )
            }
          >
            {creation?.native_targets
              .filter((row) => row.family === newFamily)
              .map((row) => (
                <option key={row.menu_cpu} value={row.menu_cpu}>
                  {row.menu_cpu}
                </option>
              ))}
          </select>
        </label>
        {creationTarget && creationTarget.members.length > 1 && (
          <label>
            {t("实际 CPU")}
            <select
              aria-label={t("实际 CPU")}
              value={newConcreteCPU}
              onChange={(e) => setNewConcreteCPU(e.target.value)}
            >
              {creationTarget.members.map((cpu) => (
                <option key={cpu} value={cpu}>
                  {cpu}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          {t("工程类型")}
          <select
            aria-label={t("工程类型")}
            value={newProjectType}
            onChange={(e) => {
              const type = creationTarget?.project_types.find(
                (row) => row.value === e.target.value,
              );
              setNewProjectType(e.target.value);
              setNewMode(
                type?.target_modes.some((mode) => mode.value === newMode)
                  ? newMode
                  : type?.target_modes[0]?.value || "",
              );
              setNewLabels(!!type?.labels_required);
            }}
          >
            {creationTarget?.project_types.map((type) => (
              <option key={type.value} value={type.value}>
                {t(type.label)}
              </option>
            ))}
          </select>
        </label>
        {creationTarget?.program_supported ? (
          <>
            <label>
              {t("程序语言")}
              <select
                aria-label={t("程序语言")}
                value={newMode}
                onChange={(e) => setNewMode(e.target.value as typeof newMode)}
              >
                {creationType?.target_modes.map((mode) => (
                  <option key={mode.value} value={mode.value}>
                    {t(mode.label)}
                  </option>
                ))}
              </select>
            </label>
            <label className="creation-labels">
              <input
                type="checkbox"
                checked={labelsRequired || (creationType?.labels_supported !== false && newLabels)}
                disabled={labelsRequired || creationType?.labels_supported === false}
                onChange={(e) => setNewLabels(e.target.checked)}
              />
              {t("使用标签")}
            </label>
          </>
        ) : (
          creationTarget && <p>{t("此型号仅支持参数工程，没有程序语言。")}</p>
        )}
        <Button
          variant="primary"
          disabled={
            busy ||
            !creationTarget ||
            !creationType ||
            (!!creationTarget.program_supported && !newMode)
          }
        >
          <Plus size={15} />
          {t("创建")}
        </Button>
      </form>
    </Modal>
  );
}
