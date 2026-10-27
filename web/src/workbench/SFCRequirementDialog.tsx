// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import {
  Plus,
  X
} from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { Button, Modal } from "../components/ui";
import type { WorkbenchModel } from "./useWorkbenchController";


export const sFCRequirementDialogFields = [
  "guarded",
  "modal",
  "setModal",
  "setPanel",
  "setText",
  "t",
] as const;
type Props = Pick<WorkbenchModel, typeof sFCRequirementDialogFields[number]>;
export function SFCRequirementDialog(props: Props) {
  const {
    guarded,
    modal,
    setModal,
    setPanel,
    setText,
    t,
  } = props;
  const [steps, setSteps] = useState([{ name: "", action: "", transition: "" }]);
  return (
    <Modal
      open={modal === "sfc"}
      onOpenChange={(v) => !v && setModal("")}
      title={t("填写流程步骤")}
    >
      <div className="sfc-steps">
        {steps.map((s, i) => (
          <div className="sfc-step" key={i}>
            <span className="step-number">{i + 1}</span>
            <div>
              {(["name", "action", "transition"] as const).map((field, n) => (
                <input
                  key={field}
                  aria-label={t(["步骤名称", "动作", "转移条件"][n])}
                  placeholder={t(["步骤名称", "动作", "转移条件"][n])}
                  value={s[field]}
                  onChange={(e) =>
                    setSteps((old) =>
                      old.map((item, j) =>
                        i === j ? { ...item, [field]: e.target.value } : item,
                      ),
                    )
                  }
                />
              ))}
            </div>
            <Button
              variant="ghost"
              aria-label={t("清除")}
              disabled={steps.length <= 1}
              onClick={() => setSteps((old) => old.filter((_, j) => i !== j))}
            >
              <X size={14} />
            </Button>
          </div>
        ))}
      </div>
      <div className="form-actions">
        <Button
          onClick={() => setSteps((old) => [...old, { name: "", action: "", transition: "" }])}
        >
          <Plus size={14} />
          {t("添加步骤")}
        </Button>
        <Button
          variant="primary"
          disabled={steps.some((s) => !s.name.trim())}
          onClick={() =>
            void guarded(async () => {
              const result = await api<{ text: string }>("/sfc/requirement", "POST", { steps });
              setText((old) => `${old}\n${result.text}`.trim());
              setModal("");
              setPanel("agent");
            })
          }
        >
          {t("应用到需求")}
        </Button>
      </div>
    </Modal>
  );
}
