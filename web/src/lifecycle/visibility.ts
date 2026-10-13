import { createContext, useContext } from "react";

export const PanelVisibility = createContext(true);
export const usePanelVisible = () => useContext(PanelVisibility);
