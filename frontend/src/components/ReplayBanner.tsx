import { useEffect, useState } from "react";

import { date } from "../lib/format";
import { getSpeed, type Session, session, setSpeed } from "../lib/replay";

/** Replay builds only: says this is a recording and sets how fast it plays. */
export function ReplayBanner() {
  const [s, setS] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [speed, set] = useState(getSpeed());
  useEffect(() => {
    session().then(setS, (e: Error) => setError(e.message));
  }, []);
  return (
    <div className="banner replay" role="note" aria-label="Replay">
      <span>
        {error ??
          (s
            ? `Replay of a recorded run from ${date(s.recorded_at)} near ${s.location} (enter it ` +
              "above), no backend or models involved. Scan, recipe " +
              `("${s.recipe.text}") and plan play the recording whatever you enter; retailer ` +
              "images are not included."
            : "Loading the recorded session\u2026")}
      </span>
      <span className="speed" role="group" aria-label="Replay speed">
        {[1, 2, 4].map((x) => (
          <button key={x} type="button" className={speed === x ? "chip active" : "chip"}
            aria-pressed={speed === x} onClick={() => {
              setSpeed(x);
              set(x);
            }}>
            {x}x
          </button>
        ))}
      </span>
    </div>
  );
}
