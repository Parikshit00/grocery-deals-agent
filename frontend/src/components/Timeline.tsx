import { AnimatePresence, motion } from "framer-motion";

import { Alert, Check, Loader, Sparkles } from "./icons";

export interface Step {
  key: string;
  label: string;
  status: "running" | "done" | "error";
  detail?: string;
  thinking?: boolean;
}

function StatusIcon({ status }: { status: Step["status"] }) {
  if (status === "done") return <Check width={15} height={15} />;
  if (status === "error") return <Alert width={15} height={15} />;
  return <Loader width={15} height={15} className="spin" />;
}

export function Timeline({ steps }: { steps: Step[] }) {
  return (
    <div className="timeline">
      <AnimatePresence initial={false}>
        {steps.map((s) => (
          <motion.div
            key={s.key}
            className={`tl-step ${s.status}`}
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22 }}
          >
            <span className="tl-ico">
              <StatusIcon status={s.status} />
            </span>
            <span className="tl-label">{s.label}</span>
            {s.detail && <span className="tl-detail">{s.detail}</span>}
            {s.status === "running" && s.thinking && (
              <span className="thinking">
                <Sparkles width={13} height={13} />
                Thinking
              </span>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
