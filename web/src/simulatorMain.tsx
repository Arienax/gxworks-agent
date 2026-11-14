// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { createRoot } from "react-dom/client";
import { SimulatorTimelineFeed } from "./features/SimulatorTimeline";
import "./styles.css";

const theme = localStorage.getItem("gx.theme");
if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
createRoot(document.getElementById("root")!).render(<SimulatorTimelineFeed endpoint="state" standalone />);
