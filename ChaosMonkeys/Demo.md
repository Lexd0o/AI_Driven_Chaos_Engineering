# Demo.md — 3-minute video script + checklist

Hard limit: **≤ 3:00**. Slide requirement: show the system injecting a failure, observing the impact, and reporting findings.

## 1. Script (real numbers from the 11 Oct runs; re-check against `reports/findings_report.md` before recording)

| Time | Screen | Voice-over (short) |
|---|---|---|
| 0:00-0:15 | Title + problem | "Chaos engineering by hand is slow and random. ChaosPilot decides what to break, breaks it safely, measures what users feel, and tells you how to fix it." |
| 0:15-0:35 | Dashboard Map tab + static audit | "It maps the OpenTelemetry Demo's services from live traces and audits their configuration. The chart ships no probes, no PodDisruptionBudgets and one replica for most services, so it predicts a weakness in almost every service." |
| 0:35-0:55 | `python -m src.run --list` (ranked plan) | "It ranks eight experiments by how much of the shop depends on each service. Every one has a hypothesis, and a policy file decides what is allowed. The AI proposes; it never touches the cluster." |
| 0:55-1:35 | Terminal: `python -m src.run --exp exp-004 --execute`, dashboard Live tab beside it | "Live: killing the cart pod. The safety gate checks the shop is healthy first, the fault goes in, the guardian watches the thresholds, and the cart journey breaks. Recovery took about 14 seconds. If anything crossed the limit, the kill switch removes the fault by itself, and the STOP button does the same by hand." |
| 1:35-2:10 | Findings report | "We ran 10 real experiments across three fault types. Of 6 we could score, 2 showed user-visible impact: the cart pod kill scored 32.7, a High finding, and the 200 ms database delay scored 82, Medium. The other 4 were tolerated, which is a result too. Each finding links to its evidence and a ready-to-apply fix as YAML." |
| 2:10-2:30 | Score + benchmark section | "Overall resilience score 86.5 out of 100, with coverage stated: 6 of 8 experiments scored, 1 stopped early by the guardian, 1 refused on purpose because it carries our own probe. Against random ordering our ranking is only slightly better; with 8 experiments that is a small sample and we say so." |
| 2:30-2:50 | (Tier 2 only if done) fix applied + re-run | "After applying the cart fix, the same experiment scores S2." Skip this row if not done. |
| 2:50-3:00 | Architecture one-liner | "AI proposes, policy decides, the executor acts, code scores. Safe by design." |

Do not say "critical": no finding reached Critical. Say High and Medium. Do not claim the CPU-stress experiments proved resilience: CPU was injected, but we had no CPU metric, so the result is "no user-visible impact".

Questions to expect, honest answers:
- Why was exp-007 not scored? The guardian stopped the frontend pod kill early (probe failure), leaving 2 samples. We report it as inconclusive instead of inventing a score.
- Why 2 repeats and not 3? Time. The report shows repeats, and a single run is one observation.
- What is the background 503? The shop shows a rare lone fast 503 even with no fault; cause not found. The pre-injection gate re-checks twice before refusing, and late blips after a fault are listed but not scored.
- Safety: policy file, can-i check, server dry-run, guardian, kill switch, detached watchdog, STOP button, forbidden targets.

## 2. Recording checklist

- [ ] Fresh baseline captured < 30 min before recording
- [ ] Load generator on, steady
- [ ] Browser zoom so text is readable at 1080p
- [ ] Dashboard + one terminal only; close everything else
- [ ] Do a dry run; time it
- [ ] Record screen + voice (OBS or similar); export MP4
- [ ] Watch it once fully; check ≤ 3:00
- [ ] Upload; test the link in an incognito window

## 3. Backups

- [ ] Record a **backup video** as soon as the Day-2 core flow works (before freeze)
- [ ] Keep a pre-generated findings report and dashboard screenshots in case the live run misbehaves during recording

## 4. Day-1 checkpoint demo (short, live)

1. Show the shop running and the dependency map.
2. Show the baseline numbers.
3. Inject one safe fault (flag or pod kill) → impact visible on the dashboard or Grafana.
4. Trigger the deliberate breach → show the automatic stop.
