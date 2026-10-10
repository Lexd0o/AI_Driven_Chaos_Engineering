# Demo.md — 3-minute video script + checklist

Hard limit: **≤ 3:00**. Slide requirement: show the system injecting a failure, observing the impact, and reporting findings.

## 1. Script

| Time | Screen | Voice-over (short) |
|---|---|---|
| 0:00–0:15 | Title + problem | "Chaos engineering by hand is slow and random. ChaosPilot decides what to break, breaks it safely, and tells you how to fix it." |
| 0:15–0:35 | Dashboard: dependency map + static audit | "It maps the OpenTelemetry Demo's services from traces, then audits configuration. It predicts N risks — for example, cart has a single replica." |
| 0:35–0:55 | Ranked plan with reasons | "It ranks experiments by business criticality and dependency position. Each has a hypothesis and a reason." |
| 0:55–1:35 | Live run: metrics spike, guardrail fires, auto-stop, recovery timer | "Injecting the top experiment. Checkout success drops… the guardrail trips at the threshold and stops the fault automatically. Recovery: X seconds." |
| 1:35–2:10 | Findings report: ranked weaknesses, predicted vs confirmed, fixes as YAML | "Of N predicted risks, A were confirmed, B refuted. Each finding cites evidence and comes with a ready-to-apply patch." |
| 2:10–2:30 | Score + benchmark | "Resilience score S with coverage. Our ranked order found the first critical weakness on experiment K, versus M on average for random order." |
| 2:30–2:50 | (Tier 2, if done) Fix applied + re-run | "After applying the fix, the same experiment scores S2." |
| 2:50–3:00 | Architecture one-liner | "AI proposes, policy decides, executor acts. Safe by design." |

All numbers in the voice-over must come from real runs (Rules.md #18).

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
