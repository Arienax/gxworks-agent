import { createRoot } from "react-dom/client";
import { SimulatorTimelineFeed } from "./features/SimulatorTimeline";
import "./styles.css";

const theme = localStorage.getItem("gx.theme");
if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
createRoot(document.getElementById("root")!).render(<SimulatorTimelineFeed endpoint="state" standalone />);
