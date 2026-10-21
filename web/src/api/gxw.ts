import type { ApiSchema } from "./client";
import { api } from "./client";

export type GXWInspection = ApiSchema["GXWInspection"];
export type GXWSourceSummary = ApiSchema["GXWSourceSummary"];
export type GXWImportResult = ApiSchema["GXWImportResult"];
export type GXWSourceView = ApiSchema["GXWSourceView"];

export const inspectGXW = (body: ApiSchema["GXWInspectionCommand"], signal?: AbortSignal) =>
  api<GXWInspection>("/gxw/inspect", "POST", body, { signal });

export const importGXW = (body: ApiSchema["GXWImportCommand"]) =>
  api<GXWImportResult>("/gxw/import", "POST", body);

export const readGXWSource = (projectId: string, versionId: string, program?: string, signal?: AbortSignal) => {
  const query = new URLSearchParams({ project_id: projectId, version_id: versionId });
  if (program) query.set("program", program);
  return api<GXWSourceView>(`/gxw/source?${query}`, "GET", undefined, { signal });
};

export const editGXW = (body: ApiSchema["GXWEditCommand"]) =>
  api<ApiSchema["Proposal"]>("/gxw/edit", "POST", body);

export function defaultGXWProgram(programs: GXWSourceSummary[]): string {
  const mainPrograms = programs.filter((source) => source.unit_kind === "program");
  return mainPrograms.length === 1 ? mainPrograms[0].source_name : "";
}
