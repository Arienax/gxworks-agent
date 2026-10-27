// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.
import { Github } from "lucide-react";
import { Modal } from "../components/ui";

export const AUTHOR_NAME = "Arienax";
export const AUTHOR_URL = "https://github.com/Arienax";
export const PROJECT_URL = "https://github.com/Arienax/gxworks-agent";
export const COPYRIGHT_NOTICE = "© 2026 Arienax";
export const LICENSE_NAME = "Apache-2.0";

export function AboutDialog({
  open,
  onClose,
  t,
}: {
  open: boolean;
  onClose: () => void;
  t: (key: string) => string;
}) {
  return (
    <Modal
      open={open}
      onOpenChange={(value) => !value && onClose()}
      title={t("关于 GXWorks Agent")}
      description={t("作者、版权与来源")}
    >
      <section className="about-dialog">
        <p>
          <strong>GXWorks Agent</strong>
        </p>
        <p className="muted">
          {t(
            "GXWorks Agent 将三菱 PLC 工程上下文、按 CPU 组织的指令事实与 GX Works2 原生操作整合为本地工具链。",
          )}
        </p>
        <dl>
          <dt>{t("版权所有")}</dt>
          <dd>{COPYRIGHT_NOTICE}</dd>
          <dt>{t("原作者")}</dt>
          <dd>
            <a href={AUTHOR_URL} target="_blank" rel="noopener noreferrer">
              {AUTHOR_NAME}
            </a>
          </dd>
          <dt>{t("原始仓库")}</dt>
          <dd>
            <a href={PROJECT_URL} target="_blank" rel="noopener noreferrer">
              <Github size={14} /> {PROJECT_URL.replace("https://", "")}
            </a>
          </dd>
          <dt>{t("许可协议")}</dt>
          <dd>{LICENSE_NAME}</dd>
        </dl>
        <p className="muted">
          {t("本工作台与发行包随附 NOTICE 文件，其中注明原作者与原始仓库地址。")}
        </p>
      </section>
    </Modal>
  );
}
