Raw outputs of the planner area-prompts measurement, 2026-11-20 (see "Results of the build" in
../2026-11-20-planner-area-prompts-design.md). Case ids and counts only: FAIL ids, "RATE id k/N"
lines and a final "N passed, M failed" line (PASS lines were filtered out at capture). No model text.

Every run: real model gpt-oss-120b, `scripts.assistant_smoke` in the throwaway container over the
staging image, 125 cases (99 old + 26 new) at --repeat 5 = 625 samples (the reruns: --repeat 10).

baseline.txt         Run 1, 13:16-13:25, commit 463e4527. Default flags (the full prompt). 602/625.
candidate1.txt       Run 2, 13:26-13:38, commit 463e4527. `--areas --no-widen`. 605/625.
candidate2.txt       Run 3, 13:50-14:02, commit 36810e8e. `--areas --no-widen`. 613/625.
candidate3_widen.txt Run 4, 14:04-14:14, commit df4a01f8. `--areas` (widening on). 613/625; 0 samples widened (see below).
candidate3.txt       Run 5, 14:14-14:25, commit df4a01f8. `--areas --no-widen`. 615/625. The final candidate.
rerun_base.txt       Three cases (hr_process-ms-3, hr_process_neg-en-5, product_stock-en-1) at --repeat 10, default
                     flags, after run 2 (commit inferred from timing; the full prompt, so the
                     commit does not affect it).
rerun_cand.txt       The same three cases at --repeat 10 with `--areas --no-widen` (commit 463e4527, inferred from
                     timing: the pair ran after run 2 and before 36810e8e at 13:50).

Compare any pair with:  python -m scripts.smoke_compare <baseline file> <candidate file>

Note on the files: they hold only FAIL lines, RATE lines and the final total; PASS lines were filtered out
when the outputs were captured. The "0 of 625 samples widened" count for candidate3_widen.txt was therefore
read from the full output at run time (the run's filter kept any line with the word `widened`, and none
appeared); it cannot be audited from the committed file. No FAIL line in that file carries `widened`.
