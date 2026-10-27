// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { createContext, useContext } from "react";

export const PanelVisibility = createContext(true);
export const usePanelVisible = () => useContext(PanelVisibility);
