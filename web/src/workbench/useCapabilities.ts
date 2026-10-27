// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useState } from "react";
import type { Session } from "../api/client";
import { api } from "../api/client";
export type CreationType = {
  value: string;
  label: string;
  labels_required: boolean;
  labels_supported?: boolean;
  target_modes: { value: string; label: string; labels_required: boolean }[];
};
export type CreationTarget = {
  menu_cpu: string;
  family: string;
  members: string[];
  default_plc_model: string;
  program_supported: boolean;
  project_types: CreationType[];
};

export type CreationCapabilities = {
  plc_model: string;
  default_target_mode: string;
  starter_requirement: string;
  target_modes: { value: string; label: string }[];
  native_targets: CreationTarget[];
};
export function useCapabilities(session: Session | null, setError: (error: string) => void) {
  const [creation, setCreation] = useState<CreationCapabilities | null>(null);
  const [defaultOperations, setDefaultOperations] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (!session?.authenticated) return;
    let active = true;
    api<{ creation: typeof creation; operations: Record<string, boolean> }>("/capabilities")
      .then((value) => {
        if (active) {
          setCreation(value.creation);
          setDefaultOperations(value.operations);
        }
      })
      .catch((error) => {
        if (active) setError(String(error instanceof Error ? error.message : error));
      });
    return () => {
      active = false;
    };
  }, [session?.authenticated]);

  return { creation, defaultOperations };
}
