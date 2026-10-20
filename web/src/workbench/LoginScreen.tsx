import {
  CircuitBoard,
  Github,
  LoaderCircle
} from "lucide-react";
import type { FormEvent } from "react";
import { Button } from "../components/ui";
import type { WorkbenchModel } from "./useWorkbenchController";


export const loginScreenFields = [
  "busy",
  "connecting",
  "error",
  "guarded",
  "login",
  "setToken",
  "t",
  "token",
] as const;
type Props = Pick<WorkbenchModel, typeof loginScreenFields[number]>;
export function LoginScreen(props: Props) {
  const {
    busy,
    connecting,
    error,
    guarded,
    login,
    setToken,
    t,
    token,
  } = props;

  return (
    <div className="login-shell">
      <div className="login-card">
        <CircuitBoard size={38} />
        <h1>
          GXWorks <span>Agent</span>
        </h1>
        <p className="eyebrow">{t("工程工作台")}</p>
        <a
          className="project-source login-project-source"
          href="https://github.com/Arienax/gxworks-agent"
          target="_blank"
          rel="noopener noreferrer"
          referrerPolicy="no-referrer"
          aria-label={t("GitHub 项目来源")}
        >
          <Github size={17} />
          <span>Arienax/gxworks-agent</span>
        </a>
        {connecting ? (
          <p>
            <LoaderCircle className="spin" size={16} /> {t("正在连接本地服务")}
          </p>
        ) : (
          <form
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              void guarded(async () => {
                await login();
              });
            }}
          >
            <h2>{t("操作员登录")}</h2>
            <p className="muted">{t("输入本地服务启动时提供的操作员令牌。")}</p>
            <input
              type="password"
              autoComplete="off"
              required
              aria-label="Operator token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
            <Button variant="primary" disabled={busy}>
              {t("连接")}
            </Button>
          </form>
        )}
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
