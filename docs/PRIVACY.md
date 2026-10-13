# Privacy and compliance

Camera-based audience measurement processes images of people in public or
semi-public spaces. This system is designed to minimise what is processed and
kept, but **the operator remains responsible for lawful deployment**. This page
is engineering guidance, not legal advice – involve your data-protection
officer or counsel.

## What the system does and does not do

| | |
|---|---|
| Images / video | processed in memory on the thin client only; never written to disk, never uploaded |
| Installation preview | optional, off by default, served on `127.0.0.1` only, not recorded |
| Face embeddings / templates | **not** computed; no face recognition or re-identification |
| Per-person record | anonymous event: start/end time, dwell, attention, age bucket, gender, bag/brand |
| Identifiers | random event ID per pass; the same person returning gets a new one |
| Retention on device | uploaded events are deleted after `storage.retention_days` (30) |

## Things to address before going live

* **Legal basis and assessment.** In the EU/UK (GDPR), estimating age and
  gender from faces is processing of personal data even if nothing is stored;
  carry out a DPIA and document your legitimate-interest assessment. Some
  jurisdictions (e.g. Illinois BIPA, Texas, Washington; parts of the EU AI Act
  regarding biometric categorisation) restrict or regulate this kind of
  analysis – check where your screens are located. Consider disabling
  demographics (`age = ""`, `gender = ""`) where it is not permitted; counting
  and attention measurement still work.
* **Transparency.** Put clear signage at each screen stating that anonymous
  audience measurement takes place, who the operator is and where to find more
  information.
* **Venue agreements.** Shopping centres and transport operators often have
  their own rules for cameras.
* **Children.** The `0-12` and `13-17` buckets exist for completeness; consider
  whether you need them and whether reports to clients should include them.
* **Access control.** Give clients the minimum role (viewer) on their own
  sub-account; review memberships regularly; rotate device keys when thin
  clients are replaced (Screens → New key).
* **Transport security.** Always run the server behind HTTPS (the provided
  compose file uses Caddy with automatic certificates).
* **Fairness.** Age/gender models have uneven accuracy across demographics.
  Report them as estimates, validate on your audience, and avoid using them for
  decisions about individuals (the system has no notion of individuals).
