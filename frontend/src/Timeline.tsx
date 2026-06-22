import { AnimatePresence, motion } from "framer-motion";

export interface Step {
  key: string;
  label: string;
  status: "running" | "done" | "error";
  detail?: string;
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
            <span className={`tl-dot ${s.status}`} />
            <span className="tl-label">{s.label}</span>
            {s.detail && <span className="tl-detail">{s.detail}</span>}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
