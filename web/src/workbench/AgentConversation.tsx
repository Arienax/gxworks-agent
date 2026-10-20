import {
  Bot
} from "lucide-react";
import type { ReactNode } from "react";
import type { WorkbenchModel } from "./useWorkbenchController";

export const agentConversationFields = [
  "t",
] as const;
type Props = Pick<WorkbenchModel, typeof agentConversationFields[number]> & { history: ReactNode; composer: ReactNode };
export function AgentConversation(props: Props) {
  const { history, composer } = props;
  const {
    t,
  } = props;

  return (
    <>
      <div className="agent-heading">
        <Bot size={20} />
        <div>
          <strong>{t("助手")}</strong>
        </div>
      </div>
      {history}

      {composer}
    </>
  );
}
