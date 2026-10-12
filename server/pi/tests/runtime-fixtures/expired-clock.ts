// Preload only in process tests: exercise the production 120-second lease with
// an accelerated monotonic clock, without a configurable production timeout.
const now=performance.now.bind(performance);
Object.defineProperty(performance,"now",{value:()=>now()*200});
