/* Runs the Tour planner's ranking off the main thread.

   Ranking every home is up to a couple of seconds at generous settings; on
   the page that would freeze the sliders. The page sends the bundle once,
   then one query per settings change, and drops any answer that arrives for
   a query it has since replaced (or terminates this worker outright if a new
   query lands while an old one is still running). */

import { rank } from "./tourplan-search.js";

let bundle = null;

self.onmessage = ({ data }) => {
  if (data.bundle) bundle = data.bundle;
  if (data.q && bundle) self.postMessage({ gen: data.gen, res: rank(bundle, data.q) });
};
