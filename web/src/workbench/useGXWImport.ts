// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useRef, useState } from "react";
import { key } from "../api/client";
import type { GXWImportResult, GXWInspection } from "../api/gxw";
import { defaultGXWProgram, importGXW, inspectGXW } from "../api/gxw";
import { LatestRead } from "../lifecycle/requests";

type Upload = { filename: string; data_base64: string };
type Phase = "" | "reading" | "inspecting" | "importing";
type Options = {
  enabled: boolean;
  onOpen: () => void;
  onClose: () => void;
  onImported: (result: GXWImportResult) => void;
  t: (message: string) => string;
};

function readFile(file: File, signal: AbortSignal): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    const abort = () => reader.abort();
    const cleanup = () => signal.removeEventListener("abort", abort);
    reader.onload = () => {
      cleanup();
      const result = typeof reader.result === "string" ? reader.result.split(",")[1] : undefined;
      if (result) resolve(result);
      else reject(new Error("无法读取 GXW 文件。"));
    };
    reader.onerror = () => { cleanup(); reject(reader.error || new Error("无法读取 GXW 文件。")); };
    reader.onabort = () => { cleanup(); reject(new DOMException("Aborted", "AbortError")); };
    signal.addEventListener("abort", abort, { once: true });
    reader.readAsDataURL(file);
  });
}

export function useGXWImport({ enabled, onOpen, onClose, onImported, t }: Options) {
  const [upload, setUpload] = useState<Upload | null>(null);
  const [filename, setFilename] = useState("");
  const [inspection, setInspection] = useState<GXWInspection | null>(null);
  const [program, setProgram] = useState("");
  const [phase, setPhaseState] = useState<Phase>("");
  const [error, setError] = useState("");
  const reads = useRef(new LatestRead());
  const importing = useRef(false);
  const activePhase = useRef<Phase>("");
  const importRequest = useRef({ id: "", program: "" });
  useEffect(() => () => reads.current.cancel(), []);

  function setPhase(value: Phase) {
    activePhase.current = value;
    setPhaseState(value);
  }

  function close() {
    if (importing.current) return;
    reads.current.cancel();
    setUpload(null);
    setFilename("");
    setInspection(null);
    setProgram("");
    setPhase("");
    setError("");
    importRequest.current = { id: "", program: "" };
    onClose();
  }

  async function open(files: FileList | readonly File[] | null) {
    if (!enabled || importing.current || !files?.length) return;
    const read = reads.current.begin();
    setUpload(null);
    setInspection(null);
    setProgram("");
    setFilename("");
    setError("");
    importRequest.current = { id: "", program: "" };
    onOpen();
    try {
      if (files.length !== 1 || !/\.gxw$/i.test(files[0].name))
        throw new Error(t("请单独选择或拖入一个 GXW 工程。"));
      const file = files[0];
      setFilename(file.name);
      if (file.size > 30 * 1024 * 1024) throw new Error(t("GXW 文件不能超过 30 MiB。"));
      setPhase("reading");
      const data_base64 = await readFile(file, read.signal);
      if (!read.current()) return;
      setPhase("inspecting");
      const value = { filename: file.name, data_base64 };
      const info = await inspectGXW(value, read.signal);
      if (!read.current()) return;
      if (!info.programs.length) throw new Error(t("工程中没有可读取的程序、功能块或函数。"));
      setUpload(value);
      setInspection(info);
      setProgram(defaultGXWProgram(info.programs));
    } catch (failure) {
      if (read.current()) setError(t(failure instanceof Error ? failure.message : String(failure)));
    } finally {
      if (read.current()) setPhase("");
    }
  }

  async function selectProgram(value: string) {
    if (!upload || importing.current) return;
    const read = reads.current.begin();
    setProgram(value);
    setError("");
    importRequest.current = { id: "", program: value };
    if (!value) { setPhase(""); return; }
    setPhase("inspecting");
    try {
      const info = await inspectGXW({ ...upload, program: value }, read.signal);
      if (read.current()) setInspection(info);
    } catch (failure) {
      if (read.current()) {
        setProgram("");
        setError(t(failure instanceof Error ? failure.message : String(failure)));
      }
    } finally {
      if (read.current()) setPhase("");
    }
  }

  async function save() {
    const source = inspection?.programs.find((item) => item.source_name === program);
    if (!enabled || importing.current || activePhase.current || !upload || !source?.readable) return;
    // A failed response can follow a completed save. Keep this request id for
    // an explicit retry; changing the selected source starts a new request.
    if (!importRequest.current.id || importRequest.current.program !== program)
      importRequest.current = { id: key(), program };
    importing.current = true;
    setPhase("importing");
    setError("");
    try {
      const result = await importGXW({ ...upload, program, request_id: importRequest.current.id });
      onImported(result);
      importing.current = false;
      close();
    } catch (failure) {
      setError(t(failure instanceof Error ? failure.message : String(failure)));
    } finally {
      importing.current = false;
      setPhase("");
    }
  }

  return { upload, filename, inspection, program, phase, error, open, close, selectProgram, save,
    busy: phase !== "" };
}
export type GXWImportModel = ReturnType<typeof useGXWImport>;
