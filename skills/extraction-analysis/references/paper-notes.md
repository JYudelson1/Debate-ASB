# Relevant paper context

Source: the user-supplied **How Does Information Access Affect LLM Monitors' Ability
to Detect Sabotage?**, `21703_How_does_information_acc.pdf`.
Page references are the PDF's printed pages. The skill works without this PDF.

- **Section 4.1, p. 5:** EaE passes selected excerpts to a separate evaluator.
  This motivates measuring retention of the actual mechanism before downstream
  scores; the repository's codebase browsing protocol has different inputs.
- **Section 5.2, p. 8:** Separate contexts matter in the paper's evaluated settings.
  Here, isolate extraction traces from later debate/judge traces and distinguish
  evidence presence from downstream sufficiency. Do not infer the same effect size.
- **Appendix E, p. 39:** Relevant behavior sometimes fails to enter the extracted
  quotes; evaluators can also rationalize suspicious behavior. This motivates
  `not_exposed` versus `exposed_not_selected` versus `selected_not_connected`.
  The present skill does not diagnose evaluator rationalization unless asked.
- **Appendix D.5, p. 35:** A stronger extractor did not consistently improve
  performance in those experiments. Model upgrades are not assumed to be the
  solution to this project's misses.

Included: readable traces, quote fidelity, mechanism/context retention, role
coverage, diagnostic exclusions and costs. Excluded: hidden attention weights,
reconstructed CoT, causal claims from correlations, end-to-end FPR/catch-rate
claims, automated prompt changes and new conditions. Those require different
observables or human-approved experimental design.

If the user asks about interventions, connect **options** to observed bottlenecks:
pipeline coverage prompts for unexposed training/config regions; provenance tracing
for opaque artifacts; independent data-flow checks for correlated generators;
keeping connected excerpts together for fragmented mechanisms. These are candidate
options, not interventions tested by this analysis or an automatic next experiment.
